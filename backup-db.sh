# MAILTO=
# SHELL=/bin/bash
TS_LABEL=$(date +%FT%s)
PATH=/bin:/usr/bin:/usr/local/sbin:/usr/sbin:/sbin:/opt/aws/bin:$HOME/.local/bin:$HOME/bin:$PATH:/usr/local/bin

[ ! -f docker-compose.yml ] && cd $HOME
docker-compose exec -T db psql -U postgres -c "SELECT pg_start_backup('$TS_LABEL', false);"
tar cjf ./backup/$TS_LABEL.tar.bz2 ./pgdata
docker-compose exec -T db psql -U postgres -c "SELECT pg_stop_backup();"
