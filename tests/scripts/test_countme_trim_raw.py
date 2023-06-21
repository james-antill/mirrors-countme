import datetime as dt
from contextlib import nullcontext
from unittest import mock

import pytest
from hypothesis import given
from hypothesis.strategies import datetimes, integers, just

from mirrors_countme import constants
from mirrors_countme.scripts import countme_trim_raw
from mirrors_countme.version import __version__

NOW_TIMESTAMP = int(dt.datetime.utcnow().timestamp())


@pytest.mark.parametrize(
    "value, expected",
    (
        ("1", 1),
        ("0", ValueError),
        ("-1", ValueError),
        ("boop", ValueError),
    ),
)
def test_positive_int(value, expected):
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            countme_trim_raw.positive_int(value)
    else:
        assert countme_trim_raw.positive_int(value) == expected


class TestParseArgs:
    def test_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            countme_trim_raw.parse_args(["--help"])
        assert exc_info.value.code == 0
        stdout, stderr = capsys.readouterr()
        assert stdout.startswith("usage:")
        assert not stderr

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            countme_trim_raw.parse_args(["--version"])
        assert exc_info.value.code == 0
        stdout, stderr = capsys.readouterr()
        assert __version__ in stdout
        assert not stderr

    def test_dbfile_missing(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            countme_trim_raw.parse_args([])
        assert exc_info.value.code != 0
        stdout, stderr = capsys.readouterr()
        assert not stdout
        assert "error: the following arguments are required" in stderr

    @pytest.mark.parametrize(
        "value, expected",
        (
            (None, False),
            ("--read-write", True),
            ("--noop", False),
        ),
    )
    def test_rw(self, value, expected):
        argv = ["test.db"]
        if value:
            argv.append(value)
        _, args = countme_trim_raw.parse_args(argv)
        assert args.rw == expected

    @pytest.mark.parametrize(
        "value, expected",
        (
            (None, False),
            ("--oldest-week", True),
        ),
    )
    def test_oldest_week(self, value, expected):
        argv = ["test.db"]
        if value:
            argv.append(value)
        _, args = countme_trim_raw.parse_args(argv)
        assert args.oldest_week == expected

    @pytest.mark.parametrize(
        "value, expected",
        (
            (None, countme_trim_raw.CONF_NON_RECENT_DURATION_WEEKS),
            ("5", 5),
        ),
    )
    def test_keep(self, value, expected):
        argv = ["test.db"]
        if value:
            argv.append(value)
        _, args = countme_trim_raw.parse_args(argv)
        assert args.keep == expected


@pytest.mark.parametrize("minmax", ("min", "max"))
def test_get_minmaxtime(minmax):
    connection = mock.Mock()
    cursor = connection.execute.return_value
    result_sentinel = object()
    cursor.fetchone.return_value = (result_sentinel,)

    result = getattr(countme_trim_raw, f"get_{minmax}time")(connection)
    assert result is result_sentinel
    connection.execute.assert_called_once_with(
        f"SELECT {minmax.upper()}(timestamp) FROM countme_raw"
    )
    cursor.fetchone.assert_called_once_with()


@given(value=integers(min_value=constants.COUNTME_START_TIME, max_value=NOW_TIMESTAMP))
def test_next_week(value):
    # Actually use a different algorithm to determine start of next week, special-casing midnights
    # of Mondays, to compare with.
    dt_value = dt.datetime.utcfromtimestamp(value).replace(tzinfo=dt.UTC)
    dt_midnight = dt_value.replace(hour=0, minute=0, second=0, microsecond=0)
    dt_monday = dt_midnight - dt.timedelta(days=dt_value.weekday())
    dt_expected = dt_monday + dt.timedelta(days=7)
    expected = int(dt_expected.timestamp())

    assert countme_trim_raw.next_week(value) == expected


def test__num_entries():
    result_sentinel = object()
    connection = mock.Mock()
    connection.execute.return_value = cursor = mock.Mock()
    cursor.fetchone.return_value = (result_sentinel,)

    result = countme_trim_raw._num_entries(connection, "trim_begin", "trim_end")

    assert result is result_sentinel
    connection.execute.assert_called_once_with(
        "SELECT COUNT(*) FROM countme_raw WHERE timestamp >= ? AND timestamp < ?",
        ("trim_begin", "trim_end"),
    )
    cursor.fetchone.assert_called_once_with()


def test__del_entries():
    connection = mock.Mock()

    countme_trim_raw._del_entries(connection, "trim_begin", "trim_end")

    connection.execute.assert_called_once_with(
        "DELETE FROM countme_raw WHERE timestamp >= ? AND timestamp < ?",
        ("trim_begin", "trim_end"),
    )
    connection.commit.assert_called_once_with()


@given(dt_value=datetimes(timezones=just(dt.UTC), allow_imaginary=False))
def test_tm2ui(dt_value):
    result = countme_trim_raw.tm2ui(dt_value.timestamp())
    assert result == dt_value.date().isoformat()


@pytest.mark.parametrize("testcase", ("rw", "rw-interrupt", "ro"))
def test_trim_data(testcase, capsys):
    expectation = nullcontext()
    connection = mock.Mock()

    with mock.patch(
        "mirrors_countme.scripts.countme_trim_raw._num_entries"
    ) as _num_entries, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.tm2ui"
    ) as tm2ui, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.time"
    ) as time, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw._del_entries"
    ) as _del_entries:
        _num_entries.return_value = "<num_affected>"
        tm2ui.side_effect = lambda v: f"<tm2ui({v})>"
        if "interrupt" in testcase:
            time.sleep.side_effect = KeyboardInterrupt()
            expectation = pytest.raises(KeyboardInterrupt)
        with expectation:
            countme_trim_raw.trim_data(
                connection=connection,
                trim_begin="trim_begin",
                trim_end="trim_end",
                rw="rw" in testcase,
            )

    stdout, _ = capsys.readouterr()

    _num_entries.assert_called_once_with(connection, "trim_begin", "trim_end")
    if "rw" in testcase:
        time.sleep.assert_called_once_with(countme_trim_raw.WARN_SECONDS)
        assert "About to DELETE data from <tm2ui(trim_begin)> to <tm2ui(trim_end)>." in stdout
        assert "This will affect <num_affected> entries." in stdout
        assert f"Interrupt within {countme_trim_raw.WARN_SECONDS} seconds to prevent that" in stdout

        if "interrupt" not in testcase:
            assert "DELETING data" in stdout
            _del_entries.assert_called_once_with(connection, "trim_begin", "trim_end")
            assert "Done." in stdout
        else:
            assert "DELETING data" not in stdout
            _del_entries.assert_not_called()
            assert "Done." not in stdout
    else:
        assert "Not deleting data from <tm2ui(trim_begin)> to <tm2ui(trim_end)>." in stdout
        assert "This would affect <num_affected> entries." in stdout
        _del_entries.assert_not_called()


@pytest.mark.parametrize("oldest_week", ("without-oldest-week", "with-oldest-week"))
def test_main(oldest_week):
    with mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.parse_args"
    ) as parse_args, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.sqlite3"
    ) as sqlite3, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.get_mintime"
    ) as get_mintime, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.get_maxtime"
    ) as get_maxtime, mock.patch(
        "mirrors_countme.scripts.countme_trim_raw.trim_data"
    ) as trim_data:
        args = mock.Mock(
            sqlite="test.db",
            keep=1,
            oldest_week=oldest_week == "with-oldest-week",
            rw=True,
        )

        parse_args.return_value = (
            object(),  # parser isn’t used
            args,
        )

        sqlite3.connect.return_value = connection = object()

        get_mintime.return_value = mintime = trim_begin = constants.COUNTME_START_TIME
        # Act as if there are 4 weeks of data in the DB.
        get_maxtime.return_value = maxtime = mintime + 4 * 7 * 24 * 3600
        if oldest_week == "without-oldest-week":
            # One week specified to keep plus one week of safety margin, and it’s a float.
            trim_end = float(maxtime - 2 * 7 * 24 * 3600)
        else:
            # This time, it’s an integer. 😁
            trim_end = mintime + 7 * 24 * 3600

        countme_trim_raw._main()

    parse_args.assert_called_once_with()
    sqlite3.connect.assert_called_once_with("file:test.db?mode=rwc", uri=True)
    get_mintime.assert_called_once_with(connection)
    get_maxtime.assert_called_once_with(connection)
    trim_data.assert_called_once_with(
        connection=connection, trim_begin=trim_begin, trim_end=trim_end, rw=True
    )


@pytest.mark.parametrize("interrupt", (False, True))
def test_cli(interrupt):
    with mock.patch("mirrors_countme.scripts.countme_trim_raw._main") as _main:
        if interrupt:
            _main.side_effect = KeyboardInterrupt()
            with pytest.raises(SystemExit) as exc_info:
                countme_trim_raw.cli()
            assert exc_info.value.code == 3
        else:
            countme_trim_raw.cli()

    _main.assert_called_once_with()
