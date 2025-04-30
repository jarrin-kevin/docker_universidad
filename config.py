import os

# Configuración para el receptor (graylog u otro)
RECEIVER_HOST = os.getenv("RECEIVER_HOST", "0.0.0.0")
RECEIVER_PORT = int(os.getenv("RECEIVER_PORT", "12345"))

CONFIG_RECEIVER = {
    "host": RECEIVER_HOST,
    "port": RECEIVER_PORT,
    "redis_url": (
        f"redis://:{os.getenv('REDIS_PASSWORD')}@"
        f"{os.getenv('REDIS_HOST', 'redis')}:"
        f"{os.getenv('REDIS_PORT', '6379')}"
    )
}


# Configuración de conexión a MySQL (sin contraseñas en texto plano)
DATABASE_CONFIG = {
    "HOST": os.getenv("MYSQL_HOST", "mysql"),
    "ROOT_USER": os.getenv("MYSQL_ROOT_USER", "root"),
    "DATABASE_NAME": os.getenv("MYSQL_DATABASE", "universidad"),
    "APP_USER": os.getenv("MYSQL_APP_USER", "app_user"),
    "PASSWORD": os.getenv("MYSQL_APP_PASSWORD"),
    "NombreTablaConexiones": os.getenv("MYSQL_TABLE_CONEXIONES", "conexiones"),
    "NombreTablaMovimientos": os.getenv("MYSQL_TABLE_MOVIMIENTOS", "movimientos")
}

# URL para conectarse a la base de datos usando SQLAlchemy
# Sin credenciales reales - serán reemplazadas en tiempo de ejecución
URL_DATABASE = {
    "DATABASE_URL": (
        f"mysql+aiomysql://"
        f"{DATABASE_CONFIG['APP_USER']}:"
        f"{DATABASE_CONFIG['PASSWORD']}@"
        f"{DATABASE_CONFIG['HOST']}/"
        f"{DATABASE_CONFIG['DATABASE_NAME']}"
    )
}


MOVEMENT_NOTIFICATION = {
    "enabled": True,
    "target_host": "host.docker.internal",
    "target_port": 12201
}