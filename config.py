import os

# Configuración para el receptor (graylog u otro)
CONFIG_RECEIVER = {
    "host": os.getenv("RECEIVER_HOST", "192.168.1.36"),
    "port": int(os.getenv("RECEIVER_PORT", "12345")),
    "redis_url": os.getenv("REDIS_URL", "redis://:x5xBAO6bdasNXKa@redis:6379")
}

# Configuración de conexión a MySQL (sin contraseñas en texto plano)
DATABASE_CONFIG = {
    "HOST": os.getenv("MYSQL_HOST", "mysql"),
    "ROOT_USER": os.getenv("MYSQL_ROOT_USER", "root"),
    "DATABASE_NAME": os.getenv("MYSQL_DATABASE", "universidad"),
    "APP_USER": os.getenv("MYSQL_APP_USER", "app_user"),
    "NombreTablaConexiones": os.getenv("MYSQL_TABLE_CONEXIONES", "conexiones"),
    "NombreTablaMovimientos": os.getenv("MYSQL_TABLE_MOVIMIENTOS", "movimientos"),
    # Rutas para cifrado RSA
    "PRIVATE_KEY_PATH": os.getenv("PRIVATE_KEY_PATH", "/run/secrets/private_key"),
    "ENCRYPTED_PASSWORD_PATH": os.getenv("ENCRYPTED_PASSWORD_PATH", "/run/secrets/mysql_password")
}

# URL para conectarse a la base de datos usando SQLAlchemy
# Sin credenciales reales - serán reemplazadas en tiempo de ejecución
URL_DATABASE = {
    "DATABASE_URL": "mysql+aiomysql://root:PASSWORD_PLACEHOLDER@{}/{}".format(
        DATABASE_CONFIG["HOST"], 
        DATABASE_CONFIG["DATABASE_NAME"]
    )
}

MOVEMENT_NOTIFICATION = {
    "enabled": True,
    "target_host": "host.docker.internal",
    "target_port": 12201
}