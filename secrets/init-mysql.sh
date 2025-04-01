#!/bin/bash

# Descifrar la contraseña
MYSQL_ROOT_PASSWORD=$(openssl pkeyutl -decrypt -inkey /run/secrets/private_key -in /run/secrets/encrypted_password 2>/dev/null | base64 -d | tr -d '\n')

# Exportar la variable para que Docker la detecte
export MYSQL_ROOT_PASSWORD

# Iniciar MySQL
exec /usr/local/bin/docker-entrypoint.sh "$@"
