# processor.py

import os
import json
import socket
import logging
import hashlib
from datetime import datetime,timedelta
import asyncio
import redis.asyncio as aioredis

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from config import URL_DATABASE, CONFIG_RECEIVER, DATABASE_CONFIG,MOVEMENT_NOTIFICATION # REDIS_URL debe estar definido, por ejemplo: "redis://localhost:6379"
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
    #Maneja el descifrado de la contraseña de MySQL usando RSA.
    
    @staticmethod
    def decrypt_password():
        try:
            # Obtener rutas de los archivos secretos
            priv_path = DATABASE_CONFIG.get('PRIVATE_KEY_PATH', '/run/secrets/private_key')
            enc_path  = DATABASE_CONFIG.get('ENCRYPTED_PASSWORD_PATH', '/run/secrets/encrypted_password')
            
            # Leer la clave privada
            with open(priv_path, 'rb') as kf:
                key = RSA.import_key(kf.read())
            
            # Leer la contraseña cifrada en modo binario
            with open(enc_path, 'rb') as pf:
                encrypted = pf.read()
                # Decodificar Base64 sin manipular el contenido
            decrypted = PKCS1_OAEP.new(key).decrypt(base64.b64decode(encrypted))
            return decrypted.decode('utf-8')
        except Exception as e:
            logging.error(f"Error descifrando contraseña: {e}")
            raise   
DB_PASSWORD = PasswordDecryptor.decrypt_password()                
class RedisConnector:
    def __init__(self):
        """Conecta a Redis y extrae mensajes de la cola."""
        try:
            self.url = CONFIG_RECEIVER["redis_url"]
            self.redis = None
        except Exception as e:
            logging.error(f"Error conectando a Redis: {e}")
            raise 
    
    async def connect(self):
        self.redis = await aioredis.from_url(self.url)
    async def get_message(self, queue='socket_messages', timeout=0):
        try:
            result = await self.redis.blpop(queue, timeout=timeout)
            if result:
                _, raw = result
                return raw
        except Exception as e:
            logging.error(f"Error obteniendo mensaje de Redis: {e}")
        return None
    async def close(self):
        await self.redis.aclose()
    async def get_key(self, key):    return await self.redis.get(key)
    async def set_key(self, key, v, ex): return await self.redis.set(key, v, ex=ex)

class DBHandler:
    """Maneja la conexión asíncrona y las operaciones SQL."""
    def __init__(self):
        try:
            # Reemplaza placeholder con la contraseña real
            db_url = URL_DATABASE['DATABASE_URL'].replace('PASSWORD_PLACEHOLDER', DB_PASSWORD)
            # Engine asíncrono (p.ej. mysql+aiomysql://...)
            self.engine = create_async_engine(
                db_url,
                echo=False,
                pool_size=10000,
                max_overflow=20000,
                pool_timeout=1,
            )
            # Session factory que produce AsyncSession
            self.Session = sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
        except Exception as e:
            logging.error(f"Error conectando a la base de datos: {e}")
            raise

    async def save_to_conexiones(self, correo, sexo, ap, campus, fecha, hora):
        """Inserta o actualiza en 'conexiones' usando upsert de MySQL."""
        async with self.Session() as sess:
            stmt = text(
                """
                INSERT INTO conexiones (correo, sexo, ap, campus, fecha, hora)
                VALUES (:correo, :sexo, :ap, :campus, :fecha, :hora)
                ON DUPLICATE KEY UPDATE
                  ap     = VALUES(ap),
                  campus = VALUES(campus),
                  hora   = VALUES(hora);
                """
            )
            params = {
                "correo": correo,
                "sexo": sexo,
                "ap": ap,
                "campus": campus,
                "fecha": fecha,
                "hora": hora
            }
            await sess.execute(stmt, params)
            await sess.commit()
            #logging.info("Registro en conexiones")

    async def save_to_movimientos(self, correo, sexo, campus_anterior, campus_actual, fecha, hora_llegada, hora_salida):
        """Inserta un registro en 'movimientos'."""
        async with self.Session() as sess:
            stmt = text(
                """
                INSERT INTO movimientos (correo, sexo, campus_anterior, campus_actual, fecha, hora_llegada, hora_salida)
                VALUES (:correo, :sexo, :campus_anterior, :campus_actual, :fecha, :hora_llegada, :hora_salida);
                """
            )
            params = {
                "correo": correo,
                "sexo": sexo,
                "campus_anterior": campus_anterior,
                "campus_actual": campus_actual,
                "fecha": fecha,
                "hora_llegada": hora_llegada,
                "hora_salida": hora_salida
            }
            await sess.execute(stmt, params)
            await sess.commit()
            #logging.info("Registro insertado en movimientos: moved %s → %s", campus_anterior, campus_actual)

    async def get_last_campus(self, correo):
        """Devuelve el último campus registrado para el usuario (o None)."""
        async with self.Session() as sess:
            result = await sess.execute(
                text(
                    "SELECT campus"
                    " FROM conexiones"
                    " WHERE correo = :correo"
                    " ORDER BY id_conexiones DESC"
                    " LIMIT 1"
                ),
                {"correo": correo}
            )
            row = result.fetchone()
            return row[0] if row else None

    async def get_last_connection_time(self, correo):
        """Devuelve la última hora de conexión registrada para el usuario (o None)."""
        async with self.Session() as sess:
            result = await sess.execute(
                text(
                    "SELECT hora"
                    " FROM conexiones"
                    " WHERE correo = :correo"
                    " ORDER BY id_conexiones DESC"
                    " LIMIT 1"
                ),
                {"correo": correo}
            )
            row = result.fetchone()
            return row[0] if row else None
    
class DataProcessor:
    """Procesa cada mensaje y aplica la lógica de negocio usando DBHandler:
        Procesa el mensaje recibido:
        - Parsea el JSON y valida los campos obligatorios.
        - Separa _ap_name_ en campus y AP.
        - Convierte el timestamp a fecha y hora.
        - Inserta un registro en 'conexiones' y, si es necesario, en 'movimientos'.
        :param message: Cadena de texto con formato JSON.
        :param session: Sesión activa de SQLAlchemy."""
    def __init__(self, db_handler, movement_notifier, redis_connector):
        self.db_handler = db_handler #llama a db_hanbler la clase que se encarga de conectarse a sql y ejecutar las sentencias sql
        self.movement_notifier = movement_notifier #es la clase encargada de enviar los datos por el puerto tcp cuando registra un cambio de campus
        self.redis = redis_connector
        self.redis_client    = redis_connector.redis   # ¡este es el cliente real!

        #detector de genero precompilado
        self.gender_detector = gender.Detector(case_sensitive=False)
        # Colas internas
        self._conn_queue = []   # para upserts en 'conexiones'
        self._mov_queue  = []   # para inserts en 'movimientos'
        # Task de flush periódico
        asyncio.create_task(self._flush_loop())   
    def _normalizar_campus(self, campus_raw):
        """Normaliza los diferentes nombres de campus a las dos categorías principales"""
        # Mapeo de alias a nombres estandarizados
        campus_map = {
            'CC': 'CENTRAL',
            'Comisariato': 'CENTRAl',
            'CBAL': 'BALZAY',
            'balzay': 'BALZAY',
            'Balzay': 'BALZAY',
            'CREDU': 'CENTRAL',
            'credu': 'CENTRAL',
            'Credu': 'CENTRAL',
            # Añadir más alias según sea necesario
        }
        return campus_map.get(campus_raw, campus_raw)
    
    def _parse_message(self,message):
        """Metodo encargado de recibir los datos y extraer la informacion necesario del mismo"""
        try:
            data = json.loads(message)
            #logging.info(f"Mensaje recibido: {data}")
            #obtener mensaje dentro de ahi tengo: ap(campus-ap),user(correo hash),timestamp
            ap = data.get('ap', '') or data.get('_message', '')
            user = data.get('user', '') or data.get('_user', '')
            timestamp = data.get('timestamp', '') or data.get('_timestamp', '')
            ts_dt  = datetime.strptime(timestamp, "%b %d %H:%M:%S %Y")
            #Seperar la fecha y la hora
            date   = ts_dt.strftime("%Y-%m-%d")
            time   = ts_dt.strftime("%H:%M:%S")
            # Extraer el nombre del usuario (correo)
            try:
                campus_raw, ap = ap.split('-', 1)
                campus = self._normalizar_campus(campus_raw)
            except Exception as e:
                logging.error(f"Error al dividir _ap_name_: {e}")
                return None
    
            # Con el correo, se procede a inferir en el genero y hashear el correo
            gender_inferred = self._infer_gender(user)
            correo_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
            return {
                'correo_hash': correo_hash,
                'gender': gender_inferred,
                'ap': ap,
                'campus': campus,
                'fecha': date,
                'hora': time
            }
        except json.JSONDecodeError as e:
            logging.error(f"Error al decodificar JSON: {e}")
            return None
            
    async def detect_movement(self, datos, last_campus):
        """Metodo encargado de preguntar si el usuario cambio de campus"""
        return last_campus and last_campus != datos['campus']
        
    async def handle_movement(self, datos, last_campus):
        """Método encargado de obtener la última conexión del usuario que cambió de campus y guardar el registro en la base"""
        hora_anterior = await self.db_handler.get_last_connection_time(datos['correo_hash'])
        if hora_anterior:
            await self.db_handler.save_to_movimientos(
                datos['correo_hash'], 
                datos['gender'], 
                last_campus, 
                datos['campus'], 
                datos['fecha'], 
                datos['hora'],
                hora_anterior
            )
            if self.movement_notifier.enabled:
                self.movement_notifier.notify_movement(
                    fecha=datos['fecha'],
                    hora_actual=datos['hora'],
                    hora_anterior=hora_anterior,
                    campus_actual=datos['campus'],
                    campus_anterior=last_campus
                )
            #logging.info(f"El usuario {datos['correo_hash']} se movilizó de {last_campus} a {datos['campus']}.")
    async def _process_connection(self, datos):
        # Aquí actualizaríamos la conexión del usuario si existe,
        # o crearíamos una nueva si no existe
        await self.db_handler.save_to_conexiones(
            datos['correo_hash'], 
            datos['gender'],
            datos['ap'],
            datos['campus'],
            datos['fecha'],
            datos['hora']
        )
    async def process_message(self, message):
        try:
            datos = self._parse_message(message)
            if not datos:
                return
            # Consultar el último campus registrado para este usuario
            correo = datos['correo_hash']
            last = await self._get_last_campus_cached(correo)
            # Si hubo movimiento, encolamos el registro de movimiento
            if last and last != datos['campus']:
                # obtenemos hora_salida antes de encolar
                hora_salida = await self.db_handler.get_last_connection_time(correo)
                self._mov_queue.append((
                    correo, datos['gender'],
                    last, datos['campus'],
                    datos['fecha'], datos['hora'],
                    hora_salida
                ))
                # notificación inmediata
                if self.movement_notifier.enabled:
                    self.movement_notifier.notify_movement(
                        fecha=datos['fecha'],
                        hora_actual=datos['hora'],
                        hora_anterior=hora_salida,
                        campus_actual=datos['campus'],
                        campus_anterior=last
                    )
                # actualizamos cache
                await self.redis_client.set(f"last_campus:{correo}", datos['campus'], ex=300)
            # Encolamos el upsert de conexión
            self._conn_queue.append((
                correo, datos['gender'], datos['ap'],
                datos['campus'], datos['fecha'], datos['hora']
            ))
            #await self._process_connection(datos)
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
    
    async def _flush_loop(self):
        while True:
            # Si la cola crece demasiado, flush inmediato          # intervalo de flush
            if len(self._conn_queue) >= 100000 or len(self._mov_queue) >= 100000:
                await self._flush_batches()
                continue
            # Si no, espera un segundo y luego flush
            await asyncio.sleep(1)
            await self._flush_batches()
    
    
    async def _flush_batches(self):
        try:
            #logging.info(">> Entrando a _flush_batches()")
            BATCH_MAX = 200000
            batch = self._conn_queue[:BATCH_MAX]
            # Flush de conexiones
            if batch:
                # abrimos una transacción en el engine
                #logging.info(f"Flushing {len(batch)} conexiones a BD")
                async with self.db_handler.engine.begin() as conn:
                    stmt_conn = text("""
                      INSERT INTO conexiones (correo, sexo, ap, campus, fecha, hora)
                      VALUES (:correo, :sexo, :ap, :campus, :fecha, :hora)
                      ON DUPLICATE KEY UPDATE
                        ap     = VALUES(ap),
                        campus = VALUES(campus),
                        hora   = VALUES(hora)
                    """)
                    result=await conn.execute(
                        stmt_conn,
                        [
                            {
                                "correo": vals[0],
                                "sexo": vals[1],
                                "ap": vals[2],
                                "campus": vals[3],
                                "fecha": vals[4],
                                "hora": vals[5]
                            }
                            for vals in batch
                        ]
                    )
                    #logging.info(f"→ Conexiones: filas afectadas = {result.rowcount}")
                del self._conn_queue[:len(batch)]
                #logging.info("→ Conexiones flush exitoso")

            # Flush de movimientos
            mov_batch = self._mov_queue[:BATCH_MAX]
            if mov_batch:
                #logging.info(f"Flushing {len(mov_batch)} movimientos a BD")
                async with self.db_handler.engine.begin() as conn:
                    stmt_mov = text("""
                    INSERT INTO movimientos (
                        correo, sexo, campus_anterior, campus_actual,
                        fecha, hora_llegada, hora_salida
                    ) VALUES (
                        :correo, :sexo, :campus_anterior, :campus_actual,
                        :fecha, :hora_llegada, :hora_salida
                    )
                    """)
                    result=await conn.execute(
                        stmt_mov,
                        [
                            {
                                "correo": vals[0],
                                "sexo": vals[1],
                                "campus_anterior": vals[2],
                                "campus_actual": vals[3],
                                "fecha": vals[4],
                                "hora_llegada": vals[5],
                                "hora_salida": vals[6]
                            }
                            for vals in mov_batch
                        ]
                    )
                    #logging.info(f"→ Movimientos: filas afectadas = {result.rowcount}")
                del self._mov_queue[:len(mov_batch)]
                #logging.info("→ Movimientos flush exitoso")
            #logging.info("<< Saliendo de _flush_batches()")
        except Exception as e:
            logging.error(f"Error en _flush_batches: {e}")
            
    def _infer_gender(self, user):
        """Infiere el género basado en el nombre del usuario."""
        try:
            local = user.split('@', 1)[0].lower()
            name = local.split('.', 1)[0]
            detected = self.gender_detector.get_gender(name)
        
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
    async def _get_last_campus_cached(self, correo_hash):
        key = f"last_campus:{correo_hash}"
        last = await self.redis_client.get(key)
        if last:
            return last.decode()    # viene en bytes
        # Si no está en cache, lo traemos de BD y lo guardamos
        last = await self.db_handler.get_last_campus(correo_hash)
        if last:
            await self.redis_client.set(key, last, ex=300)
        return last
class MovementNotifier:
    """Clase que maneja el envío de notificaciones TCP cuando un usuario se mueve entre campus."""
    
    def __init__(self):
        """Inicializa el notificador con la configuración especificada."""
        self.enabled = MOVEMENT_NOTIFICATION.get("enabled", False)
        self.target_host = MOVEMENT_NOTIFICATION.get("target_host", "host.docker.internal")
        self.target_port = MOVEMENT_NOTIFICATION.get("target_port", 12201)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) 
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
        # Formatear hora_actual
        #hora_actual_str = hora_actual.strftime("%H:%M:%S")
        hora_salida  = str(hora_anterior)
        hora_llegada = str(hora_actual)
        fecha_iso = f"{fecha}T{hora_llegada}"
        try:
            # Crear el mensaje JSON con los datos del movimiento
            movement_data = {
                "fecha": fecha_iso,
                "hora_llegada": hora_llegada,
                "hora_salida": hora_salida,
                "campus_actual": campus_actual,
                "campus_anterior": campus_anterior,
                "source": "processor"
            }
            
            # Convertir a JSON y añadir un salto de línea para delimitar mensajes
            message = json.dumps(movement_data) + "\n"           
            # Enviar datos - para UDP no necesitamos conectar primero
            self.sock.sendto(message.encode('utf-8'), (self.target_host, self.target_port))
            #logging.info(f"Mensaje UDP enviado: {message}")
            #self.sock.close()
            
            logging.info(f"Notificación de movimiento enviada: {campus_anterior} → {campus_actual}")

            #logging.info(f"Se estan enviado los datos a : {self.target_host} → {self.target_port}")
        except Exception as e:
            logging.error(f"Error al enviar notificación de movimiento: {e}")

async def main():
    try:
        redis_connector = RedisConnector()
        await redis_connector.connect()

        db_handler = DBHandler()
        movement_notifier = MovementNotifier()  # Crear instancia
        data_processor = DataProcessor(db_handler,movement_notifier,redis_connector)
        
        logging.info("Iniciando procesador de mensajes...")
        
        while True:
            message = await redis_connector.get_message("socket_messages", timeout=0)
            if message:
                await data_processor.process_message(message)
                
    except KeyboardInterrupt:
        logging.info("Proceso interrumpido por el usuario.")
    except Exception as e:
        logging.error(f"Error en el procesamiento principal: {e}")
    finally:
        await redis_connector.close()

if __name__ == "__main__":
    asyncio.run(main())
