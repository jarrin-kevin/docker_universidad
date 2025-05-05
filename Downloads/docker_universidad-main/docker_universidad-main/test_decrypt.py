from Cryptodome.PublicKey import RSA
from Cryptodome.Cipher import PKCS1_OAEP
import base64

def decrypt_password(private_key_path, encrypted_password_path):
    try:
        # Leer clave privada
        with open(private_key_path, 'rb') as key_file:
            private_key = RSA.import_key(key_file.read())
        
        # Leer contraseña cifrada
        with open(encrypted_password_path, 'rb') as pass_file:
            encrypted_data = base64.b64decode(pass_file.read())
        
        # Descifrar
        cipher = PKCS1_OAEP.new(private_key)
        decrypted_password = cipher.decrypt(encrypted_data)
        
        return decrypted_password.decode('utf-8')
    
    except Exception as e:
        print(f"Error al descifrar: {e}")
        return None

# Rutas de archivos
private_key_path = './secrets/private_key.pem'
encrypted_password_path = './secrets/encrypted_password.txt'

# Descifrar
password = decrypt_password(private_key_path, encrypted_password_path)
print("Contraseña descifrada:", password)