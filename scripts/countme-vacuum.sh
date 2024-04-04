#! /bin/sh -e

cd /var/lib/countme/

conf_CENTOS=false

if [ "x$1" = "xcentos" ]; then
	shift
	conf_CENTOS=true
fi

if $conf_CENTOS; then
suffix="-centos"
else
suffix=""
fi

if [ "x$(whoami)" != "xcountme" ]; then
  echo "Need to be run as countme."
  exit 1
fi

echo "Dir: $conf_LOGDIR"
echo "Pkg: $(rpm -q python3.11-mirrors-countme)"

rawdb="/var/lib/countme/raw$suffix.db"
echo " DB: $(ls -sh $rawdb)"

function superVacuum {
    rm -f ${rawdb}.dump.sql.tmp
    sqlite3 ${rawdb} '.dump' > ${rawdb}.dump.sql.tmp
    rm -f ${rawdb}.restore.tmp
    sqlite3 ${rawdb}.restore.tmp < ${rawdb}.dump.sql.tmp
    mv ${rawdb}.restore.tmp ${rawdb}
    rm -f ${rawdb}.dump.sql.tmp
}

cmd="error"

case "x$1" in
	x) cmd="vacuum" ;;
	xvacuum) cmd="vacuum" ;;
	xSuperVacuum) cmd="supervacuum" ;;
	xsuperVacuum) cmd="supervacuum" ;;
	xsupervacuum) cmd="supervacuum" ;;
	*) echo "Unknown command"; exit 1 ;;
esac

if [ ! -f ${rawdb} ]; then
  echo "Raw DB doesnt exit!"
  exit 2
fi

case "$cmd" in
	vacuum) echo "VACUUM"; sqlite3 ${rawdb} 'VACUUM;' ;;
	supervacuum) echo "Super VACUUM"; superVacuum ;;
	*) echo "ERROR"; exit 9 ;;
esac

echo "NDB: $(ls -sh $rawdb)"
