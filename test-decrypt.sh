#!/bin/bash

# Este script verifica el proceso de descifrado sin modificar nada
echo "Intentando descifrar con openssl..."
DECRYPT=$(openssl pkeyutl -decrypt -inkey ./secrets/private_key.pem -in ./secrets/encrypted_password.txt 2>/dev/null | base64 -d)

if [ -z "$DECRYPT" ]; then
  echo "ERROR: El descifrado falló o produjo una cadena vacía"
  exit 1
else
  echo "Descifrado exitoso."
  echo "Añade esta línea a tu docker-compose:"
  echo "      - MYSQL_ROOT_PASSWORD_FILE=/run/secrets/mysql_root_password"
  echo "Y crea un nuevo archivo secrets:"
  echo "  mysql_root_password:"
  echo "    file: ./secrets/mysql-password.txt"
fi
