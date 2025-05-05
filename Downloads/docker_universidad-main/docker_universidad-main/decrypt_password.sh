#!/bin/bash

# Descifrar contraseña
DECRYPTED=$(python3 -c "
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
import sys

try:
    with open('/run/secrets/private_key', 'rb') as key_file:
        private_key = RSA.import_key(key_file.read())
    
    with open('/run/secrets/encrypted_password', 'rb') as password_file:
        encrypted_data = password_file.read()
        encrypted_password = base64.b64decode(encrypted_data)
    
    cipher = PKCS1_OAEP.new(private_key)
    decrypted_password = cipher.decrypt(encrypted_password)
    print(decrypted_password.decode('utf-8'))
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    exit(1)
")

# Guardar la contraseña descifrada en un archivo temporal
echo "$DECRYPTED" > /tmp/mysql_password
chmod 600 /tmp/mysql_password

# Exportar como variable de entorno para MySQL
export MYSQL_ROOT_PASSWORD="$DECRYPTED"
