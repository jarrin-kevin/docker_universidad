#!/bin/bash

# Descifrar la contraseña usando openssl
PLAIN_PASSWORD=$(openssl pkeyutl -decrypt -inkey /run/secrets/private_key -in /run/secrets/encrypted_password 2>/dev/null | base64 -d | tr -d '\n')

# Configurar la variable de entorno para MySQL
export MYSQL_ROOT_PASSWORD="$PLAIN_PASSWORD"

# Iniciar MySQL
exec /usr/local/bin/docker-entrypoint.sh "$@"
