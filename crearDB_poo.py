import mysql.connector
import os
from mysql.connector import errorcode
from CrearTablasDb import ConexionTable, MovimientoTable,EventoLimpiarConexiones # Importar las tablas 
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
import time
import sys
import traceback

# Al principio del script
print("Iniciando script de creación de base de datos...", file=sys.stderr)
sys.stderr.flush()  # Forzar la salida inmediata
class DatabaseConfig:
    try:
        
        print(f"Intentando leer clave privada desde: {'/run/secrets/private_key'}")
        with open("/run/secrets/private_key", "rb") as key_file:
            private_key = RSA.import_key(key_file.read())
            print("Clave privada leída correctamente")

        print(f"Intentando leer contraseña cifrada desde: {'/run/secrets/encrypted_password'}")  
        with open("/run/secrets/encrypted_password", "rb") as pass_file:
            encrypted_data = base64.b64decode(pass_file.read())
        print("Contraseña cifrada leída correctamente")

        cipher = PKCS1_OAEP.new(private_key)
        ROOT_PASSWORD = cipher.decrypt(encrypted_data).decode('utf-8')
        print("Contraseña descifrada correctamente")
        #print(f"Contraseña descifrada: {ROOT_PASSWORD}")  # Log temporal
    except Exception as e:
        print(f"Error descifrando contraseña: {e}")
        import traceback
        traceback.print_exc()
        ROOT_PASSWORD = None
        
    HOST = os.getenv("MYSQL_HOST", "mysql")
    ROOT_USER = os.getenv("MYSQL_ROOT_USER", "root")
    DATABASE_NAME = os.getenv("MYSQL_DATABASE", "universidad")
    

class DatabaseSetup:
    """Clase para gestionar la conexión, creación de base de datos y tablas."""
    def __init__(self): # CONSTRUCTOR
        self.connection = None # declarar la variable en un ámbito que abarque el bloque try y el finally, para saber si esta conectada la base de datos.
        self.cursor = None # El cursor es un objeto que permite ejecutar sentencias SQL y recorrer los resultados que devuelve la base de datos. 
        
        # Lista de modelos a crear, bueno creo que esto deberia ser automatico o no?
        self.tables = [ConexionTable(), MovimientoTable(),EventoLimpiarConexiones()]

    def connect(self):
        """Establece la conexión con el servidor MySQL."""
        try:
            # se establece la conexión a MySQL usando las credenciales del usuario root.Esto es necesario para tener permisos administrativos
            self.connection = mysql.connector.connect(
                host=DatabaseConfig.HOST,
                user=DatabaseConfig.ROOT_USER,
                password=DatabaseConfig.ROOT_PASSWORD
            )
            # Crear cursor, junto con la conexion
            self.cursor = self.connection.cursor()
            print("Conexión a MySQL establecida.")
        except mysql.connector.Error as err:
            print(f"Error conectando a MySQL: {err}")
            raise
    

    def execute_query(self,query,description=None):
        """Ejecutar una consulta y mostrar mensaje de éxito si se proporciona descripción."""
        try:
            self.cursor.execute(query)
            if description:
                print(description)#proporcionar un mensaje descriptivo, se pueda pasar una cdena de texto que explique la query 
        except mysql.connector.Error as err:
            print(f"Error ejecutando '{description}': {err}")
            raise

    


    def setup_database(self):
        """Crea la base de datos, selecciona la DB, crea las tablas y configura el usuario."""
        try:
            self.connect() #Aqui llamo la funcion connect

            
            # Seleccionar la base de datos
            use_db_query = f"USE {DatabaseConfig.DATABASE_NAME};"
            self.execute_query(use_db_query, f"Usando base de datos '{DatabaseConfig.DATABASE_NAME}'.")
            self.execute_query("SET GLOBAL event_scheduler = ON;", "Event scheduler activado.")
            # Crear las tablas definidas en los modelos
            for table in self.tables:
                table.create_table(self.cursor)
            self.execute_query(
                """
                ALTER TABLE conexiones
                  ADD UNIQUE INDEX ux_conexiones_correo_fecha (correo, fecha);
                """,
                "Índice único ux_conexiones_correo_fecha creado."
            )
            # Confirmar la transacción
            self.connection.commit()
    
        except mysql.connector.Error as err:
            print(f"Error durante la configuración: {err}")
            if self.connection and self.connection.is_connected():
                self.connection.rollback()
        finally:
            self.close()

    def close(self):
        """Cierra el cursor y la conexión para liberar recursos."""
        if self.cursor:
            self.cursor.close()
        if self.connection and self.connection.is_connected():
            self.connection.close()
        print("Conexión cerrada.")

if __name__ == "__main__":
    try:
        print("Intentando inicializar base de datos...", file=sys.stderr)
        sys.stderr.flush()
        db_setup = DatabaseSetup()
        db_setup.setup_database()
        print("Proceso de inicialización completado con éxito.", file=sys.stderr)
    except Exception as e:
        print(f"Error fatal en la inicialización: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)  # Terminar con código de error
