# processor.py
import os
import json
import socket
import logging
from datetime import datetime,timedelta
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import hashlib
from config import URL_DATABASE, CONFIG_RECEIVER, DATABASE_CONFIG,MOVEMENT_NOTIFICATION # REDIS_URL debe estar definido, por ejemplo: "redis://localhost:6379"
import hashlib
import gender_guesser.detector as gender
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import base64
import re
"""
Separar la funcionalidad en tres clases:

RedisConnector: se encarga de conectarse a Redis y extraer mensajes de la cola.
DBHandler: se encarga de la conexión a la base de datos y de ejecutar las operaciones (inserciones y consultas).
DataProcessor: recibe un mensaje, lo procesa (parsea, valida, extrae campos) y utiliza a DBHandler para guardar la información en las tablas según la lógica de negocio.
"""



# Configuración del logging, mensajes informativos en pantalla
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
class PasswordDecryptor:
    """Maneja el descifrado de la contraseña de MySQL usando RSA."""
    
    @staticmethod
    def decrypt_password():
        try:
            # Obtener rutas de los archivos secretos
            private_key_path = DATABASE_CONFIG.get("PRIVATE_KEY_PATH", "/run/secrets/private_key")
            encrypted_password_path = DATABASE_CONFIG.get("ENCRYPTED_PASSWORD_PATH", "/run/secrets/encrypted_password")
            
            logging.info(f"Descifrando contraseña usando clave privada en: {private_key_path}")
            
            # Leer la clave privada
            with open(private_key_path, 'rb') as key_file:
                private_key = RSA.import_key(key_file.read())
            
            # Leer la contraseña cifrada en modo binario
            with open(encrypted_password_path, 'rb') as password_file:
                encrypted_data = password_file.read()
                # Decodificar Base64 sin manipular el contenido
                encrypted_password = base64.b64decode(encrypted_data)
            
            # Descifrar usando PKCS1_OAEP
            cipher = PKCS1_OAEP.new(private_key)
            decrypted_password = cipher.decrypt(encrypted_password)
            
            logging.info("Contraseña descifrada correctamente")
            return decrypted_password.decode('utf-8')
        except Exception as e:
            logging.error(f"Error al descifrar la contraseña: {e}")
            raise RuntimeError(f"No se pudo descifrar la contraseña: {e}")
                               
class RedisConnector:
    def __init__(self):
        """Conecta a Redis y extrae mensajes de la cola."""
        try:
            # Usar CONFIG_RECEIVER del módulo config
            redis_url = CONFIG_RECEIVER["redis_url"]
            self.redis_client = redis.from_url(redis_url)
            self.redis_client.ping()
            logging.info("Conexión a Redis establecida.")
        except Exception as e:
            logging.error(f"Error conectando a Redis: {e}")
            raise 
        
    def get_message(self, queue_name="socket_messages", timeout=0):
        """Espera y extrae un mensaje de la cola Redis."""
        try:
            message_data = self.redis_client.blpop(queue_name, timeout=timeout)
            if message_data:
                _, message = message_data
                return message.decode("utf-8")
        except Exception as e:
            logging.error(f"Error al extraer mensaje de Redis: {e}")
        return None  


class DBHandler:
    """Maneja la conexión a la base de datos y las operaciones SQL."""
    def __init__(self):
        try:
            # Obtener la contraseña descifrada
            decrypted_password = PasswordDecryptor.decrypt_password()
            
            # Construir la URL de conexión con la contraseña descifrada
            database_url = URL_DATABASE["DATABASE_URL"]
            
            # Reemplazar el placeholder con la contraseña descifrada
            database_url = database_url.replace("PASSWORD_PLACEHOLDER", decrypted_password)
            
            self.engine = create_engine(database_url)
            self.Session = sessionmaker(bind=self.engine)
            self.session = self.Session()
            logging.info("Conexión a la base de datos establecida.")
        except Exception as e:
            logging.error(f"Error conectando a la base de datos: {e}")
            raise e
    def save_to_conexiones(self, correo,sexo, ap, campus, fecha, hora):
        # Verificar si existe
        check_query = text("""
        SELECT id_conexiones FROM conexiones 
        WHERE correo = :correo AND fecha = :fecha
        """)
        result = self.session.execute(check_query, {"correo": correo, "fecha": fecha}).fetchone()
        if result:
            #actualizar hora del usuario
            update_query = text("""
                UPDATE conexiones 
                SET ap = :ap, campus = :campus, hora = :hora
                WHERE correo = :correo AND fecha = :fecha
            """)
            self.session.execute(update_query, 
                {"correo": correo, "ap": ap, "campus": campus, "hora": hora, "fecha": fecha})
            logging.info("Registro actualizado en conexiones.")
        else:
            #Insertar nuevo conexion
            insert_query = text("""
            INSERT INTO conexiones (correo, sexo, ap, campus, fecha, hora)
            VALUES (:correo, :sexo, :ap, :campus, :fecha, :hora)
            """)    
            self.session.execute(insert_query, 
                {"correo": correo, "sexo": sexo, "ap": ap, "campus": campus, "fecha": fecha, "hora": hora})
            logging.info("Registro insertado en conexiones.")
    def save_to_movimientos(self, correo, sexo, campus_anterior, campus_actual, fecha, hora_llegada, hora_salida):
        query = text("""
            INSERT INTO movimientos (correo, sexo, campus_anterior, campus_actual, fecha, hora_llegada, hora_salida)
            VALUES (:correo, :sexo, :campus_anterior, :campus_actual, :fecha, :hora_llegada, :hora_salida)
        """)
        self.session.execute(query, {"correo": correo, "sexo": sexo, "campus_anterior": campus_anterior, 
                                    "campus_actual": campus_actual, "fecha": fecha, 
                                    "hora_llegada": hora_llegada, "hora_salida": hora_salida})
        logging.info("Registro insertado en movimientos.")
    

    def get_last_campus(self, correo):
        select_query = text("""
            SELECT campus FROM conexiones
            WHERE correo = :correo
            ORDER BY id_conexiones DESC LIMIT 1
        """)
        result = self.session.execute(select_query, {"correo": correo}).fetchone()
        logging.info(f"Resultado de consulta get_last_campus: {result}")
        return result[0] if result else None
    
    def get_last_connection_time(self, correo):
        select_query = text("""
            SELECT hora FROM conexiones
            WHERE correo = :correo
            ORDER BY id_conexiones DESC LIMIT 1
        """)
        result = self.session.execute(select_query, {"correo": correo}).fetchone()
        logging.info(f"Resultado de consulta get_last_connection_time: {result}")
        return result[0] if result else None

    def commit(self):
        self.session.commit()
    def rollback(self):
        self.session.rollback()
    def close(self):
        self.session.close()
        logging.info("Sesión de la base de datos cerrada.")
    
class DataProcessor:
    """Procesa cada mensaje y aplica la lógica de negocio usando DBHandler:
        Procesa el mensaje recibido:
        - Parsea el JSON y valida los campos obligatorios.
        - Separa _ap_name_ en campus y AP.
        - Convierte el timestamp a fecha y hora.
        - Inserta un registro en 'conexiones' y, si es necesario, en 'movimientos'.
        :param message: Cadena de texto con formato JSON.
        :param session: Sesión activa de SQLAlchemy."""
    def __init__(self, db_handler, movement_notifier):
        self.db_handler = db_handler #llama a db_hanbler la clase que se encarga de conectarse a sql y ejecutar las sentencias sql
        self.movement_notifier = movement_notifier #es la clase encargada de enviar los datos por el puerto tcp cuando registra un cambio de campus
    def _parse_message(self,message):
        """Metodo encargado de recibir los datos y extraer la informacion necesario del mismo"""
        try:
            data = json.loads(message)
            logging.info(f"Mensaje recibido: {data}")

            # Extraer campos obligatorios
            timestamp = data.get("timestamp", "N/A")
            dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
            #obtener mensaje dentro de ahi tengo: ap(campus-ap),user(correo hash)
            message = data.get('message', '')
            # Extraer campus (nombre del punto de acceso)
            ap_match = re.search(r'AP:([\w\-\d\-]+)', message)
            apmessage = ap_match.group(1) if ap_match else None
            try:
                campus,ap =  apmessage.split('-', 1)
            except Exception as e:
                logging.error(f"Error al dividir _ap_name_: {e}")
                return
            # Extraer nombre de usuario (correo electrónico)
            email_match = re.search(r'user-([\w.]+@[\w.]+)', message)
            user = email_match.group(1) if email_match else None
            # Con el correo, se procede a inferir en el genero y hashear el correo
            gender_inferred = self._infer_gender(user)
            correo_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
            return {
                'correo_hash': correo_hash,
                'gender': gender_inferred,
                'ap': ap,
                'campus': campus,
                'fecha': dt.date(),
                'hora': dt.time()
            }
        except json.JSONDecodeError as e:
            logging.error(f"Error al decodificar JSON: {e}")
            return None
            
    def _detect_movement(self, datos, last_campus):
        """Metodo encargado de preguntar si el usuario cambio de campus"""
        if last_campus and last_campus != datos['campus']:
            logging.info(f"Campus actual: {datos['campus']}, Último campus: {last_campus}")
            return True
        return False
        
    def _handle_movement(self, datos, last_campus):
        """Método encargado de obtener la última conexión del usuario que cambió de campus y guardar el registro en la base"""
        hora_anterior = self.db_handler.get_last_connection_time(datos['correo_hash'])
        if hora_anterior:
            self.db_handler.save_to_movimientos(
                datos['correo_hash'], 
                datos['gender'], 
                last_campus, 
                datos['campus'], 
                datos['fecha'], 
                datos['hora'],
                hora_anterior
            )
            if self.movement_notifier:
                self.movement_notifier.notify_movement(
                    fecha=datos['fecha'],
                    hora_actual=datos['hora'],
                    hora_anterior=hora_anterior,
                    campus_actual=datos['campus'],
                    campus_anterior=last_campus
                )
            logging.info(f"El usuario {datos['correo_hash']} se movilizó de {last_campus} a {datos['campus']}.")
    def _process_connection(self, datos):
        # Aquí actualizaríamos la conexión del usuario si existe,
        # o crearíamos una nueva si no existe
        self.db_handler.save_to_conexiones(
            datos['correo_hash'], 
            datos['gender'],
            datos['ap'],
            datos['campus'],
            datos['fecha'],
            datos['hora']
        )
    def process_message(self, message):
        try:
            datos = self._parse_message(message)
            if not datos:
                return
            # Consultar el último campus registrado para este usuario
            last_campus = self.db_handler.get_last_campus(datos['correo_hash'])
            # Si hay registro previo y el campus es distinto, registrar el movimiento
            if self._detect_movement(datos, last_campus): #un if para llamar un metodo que tiene otro if?
                self._handle_movement(datos, last_campus) #guardar en movimientos
            self._process_connection(datos)
            self.db_handler.commit()
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
            self.db_handler.rollback()
    def _infer_gender(self, user):
        """Infiere el género basado en el nombre del usuario."""
        try:
            local_part = user.split('@')[0]
            name = local_part.split('.')[0].lower()
            detector = gender.Detector(case_sensitive=False)
            detected = detector.get_gender(name)
        
            if detected in ("male", "mostly_male"):
                return "hombre"
            elif detected in ("female", "mostly_female"):
                return "mujer"
            elif detected == "andy":
                # Para nombres andróginos, usamos heurísticas adicionales
                if name.endswith('a'):
                    return "mujer"
                elif name.endswith(('o', 'or')):
                    return "hombre"
                else:
                    return "desconocido"
            else:
                # Intenta con heurísticas para español cuando gender_guesser falla
                if name.endswith('a') and len(name) > 2:
                    return "mujer"
                elif name.endswith(('o', 'or', 'io', 'el')) and len(name) > 2:
                    return "hombre"
                else:
                    return "desconocido"
        except Exception:
            return "desconocido"

class MovementNotifier:
    """Clase que maneja el envío de notificaciones TCP cuando un usuario se mueve entre campus."""
    
    def __init__(self):
        """Inicializa el notificador con la configuración especificada."""
        self.enabled = MOVEMENT_NOTIFICATION.get("enabled", False)
        self.target_host = MOVEMENT_NOTIFICATION.get("target_host", "host.docker.internal")
        self.target_port = MOVEMENT_NOTIFICATION.get("target_port", 12201)
        
        if self.enabled:
            logging.info(f"Notificador de movimientos configurado: {self.target_host}:{self.target_port}")
        else:
            logging.info("Notificador de movimientos desactivado")
    
    def notify_movement(self, fecha, hora_actual, hora_anterior, campus_actual, campus_anterior):
        """
        Envía una notificación TCP cuando un usuario se mueve entre campus.
        
        Args:
            fecha (str): Fecha del movimiento (YYYY-MM-DD)
            hora_actual (str): Hora actual del movimiento (HH:MM:SS)
            hora_anterior (str): Hora de la conexión anterior (HH:MM:SS)
            campus_actual (str): Campus actual del usuario
            campus_anterior (str): Campus anterior del usuario
        """
 # Primero verifica si hora_anterior es una tupla y extraer el primer elemento si es necesario
        if isinstance(hora_anterior, tuple) and len(hora_anterior) > 0:
            hora_anterior = hora_anterior[0]
            
        if isinstance(hora_anterior, timedelta):
            hora_anterior_str = str(hora_anterior)
        else:
            hora_anterior_str = hora_anterior.strftime("%H:%M:%S.%f") if hasattr(hora_anterior, 'strftime') else str(hora_anterior)
            
        # Formatear hora_actual
        hora_actual_str = hora_actual.strftime("%H:%M:%S")
        if not self.enabled:
            logging.debug("Notificaciones desactivadas, no se enviará notificación de movimiento")
            return
        fecha = f"{fecha} {hora_actual_str}"
        try:
            # Crear el mensaje JSON con los datos del movimiento
            movement_data = {
                "fecha": datetime.strptime(str(fecha), "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "hora_llegada": hora_actual_str,
                "hora_salida": hora_anterior_str,
                "campus_actual": campus_actual,
                "campus_anterior": campus_anterior,
                "source": "processor"
            }
            
            # Convertir a JSON y añadir un salto de línea para delimitar mensajes
            message = json.dumps(movement_data) + "\n"
            
            # Crear socket TCP
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # Conectar al host y puerto configurados
            sock.connect((self.target_host, self.target_port))
            
            # Enviar datos
            sock.sendall(message.encode('utf-8'))
            logging.info(f"Mensaje enviado: {message}")
            
            # Cerrar la conexión
            sock.close()
            
            logging.info(f"Notificación de movimiento enviada: {campus_anterior} → {campus_actual}")
            logging.info(f"Se estan enviado los datos a : {self.target_host} → {self.target_port}")
        except Exception as e:
            logging.error(f"Error al enviar notificación de movimiento: {e}")

def main():
    try:
        redis_connector = RedisConnector()
        db_handler = DBHandler()
        movement_notifier = MovementNotifier()  # Crear instancia
        data_processor = DataProcessor(db_handler,movement_notifier)
        
        logging.info("Iniciando procesador de mensajes...")
        
        while True:
            message = redis_connector.get_message("socket_messages", timeout=0)
            if message:
                data_processor.process_message(message)
                
    except KeyboardInterrupt:
        logging.info("Proceso interrumpido por el usuario.")
    except Exception as e:
        logging.error(f"Error en el procesamiento principal: {e}")
    finally:
        if 'db_handler' in locals():
            db_handler.close()

if __name__ == "__main__":
    main()
