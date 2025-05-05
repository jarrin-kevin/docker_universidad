# processor.py

from concurrent.futures import ThreadPoolExecutor
import signal
import socket
import logging
import hashlib
from datetime import datetime
import asyncio
import redis.asyncio as aioredis

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from config import URL_DATABASE, CONFIG_RECEIVER, DATABASE_CONFIG,MOVEMENT_NOTIFICATION # REDIS_URL debe estar definido, por ejemplo: "redis://localhost:6379"
import gender_guesser.detector as gender

from cachetools import TTLCache, cached
import orjson as json

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
_GENDER_CACHE = TTLCache(maxsize=1024, ttl=360)

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
    async def cache_last_campus(self, correo, campus, ex=300):
        #Guarda el último campus del usuario en Redis con un tiempo de expiración.
        await self.redis.set(f"last_campus:{correo}", campus, ex=ex)
    async def get_last_campus(self, correo):
        #Devuelve el último campus del usuario desde Redis.
        val = await self.redis.get(f"last_campus:{correo}")
        return val.decode() if val else None
    async def close(self):
        await self.redis.aclose()


class DBHandler:
    """Maneja la conexión asíncrona y las operaciones SQL."""
    def __init__(self):
        try:
            password = DATABASE_CONFIG['PASSWORD']
            if not password:
                raise ValueError("La variable MYSQL_APP_PASSWORD no está definida en el entorno.")
            # Reemplaza placeholder con la contraseña real
            db_url = URL_DATABASE['DATABASE_URL']
            # Engine asíncrono (p.ej. mysql+aiomysql://...)
            self.engine = create_async_engine(
                db_url,
                echo=False,
                 connect_args={"auth_plugin": "mysql_native_password"},
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
            self.STMT_CONEX = text("""
                INSERT INTO conexiones (correo, sexo, ap, campus, fecha, hora)
                VALUES (:correo, :sexo, :ap, :campus, :fecha, :hora)
                ON DUPLICATE KEY UPDATE
                    ap     = VALUES(ap),
                    campus = VALUES(campus),
                    hora   = VALUES(hora)
            """)
            self.STMT_MOV = text("""
            INSERT INTO movimientos (
                correo, sexo, campus_anterior, campus_actual,
                fecha, hora_llegada, hora_salida
            )
            VALUES (:correo, :sexo, :campus_anterior, :campus_actual,
                :fecha, :hora_llegada, :hora_salida)
        """)
        except Exception as e:
            logging.error(f"Error conectando a la base de datos: {e}")
            raise
    async def bulk_upsert_conexiones(self, batch):
        async with self.engine.begin() as conn:
            await conn.execute(self.STMT_CONEX, [
                {"correo": c, "sexo": s, "ap": a, "campus": cp,
                 "fecha": f, "hora": h}
                for c, s, a, cp, f, h in batch
            ])
    async def bulk_insert_movimientos(self, batch):
        async with self.engine.begin() as conn:
            await conn.execute(self.STMT_MOV, [
                {"correo": c, "sexo": s, "campus_anterior": ca,
                 "campus_actual": cn, "fecha": f,
                 "hora_llegada": hl, "hora_salida": hs}
                for c, s, ca, cn, f, hl, hs in batch
            ])

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
    async def close(self): 
        await self.engine.dispose()
class DataProcessor:
    """Procesa cada mensaje y aplica la lógica de negocio usando DBHandler:
        Procesa el mensaje recibido:
        - Parsea el JSON y valida los campos obligatorios.
        - Separa _ap_name_ en campus y AP.
        - Convierte el timestamp a fecha y hora.
        - Inserta un registro en 'conexiones' y, si es necesario, en 'movimientos'.
        :param message: Cadena de texto con formato JSON.
        :param session: Sesión activa de SQLAlchemy."""
    _MONTHS = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5,
        'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10,
        'Nov': 11, 'Dec': 12
    }
   

    def __init__(self, db_handler, movement_notifier, redis_connector):
        self.db_handler = db_handler #llama a db_hanbler la clase que se encarga de conectarse a sql y ejecutar las sentencias sql
        self.movement_notifier = movement_notifier #es la clase encargada de enviar los datos por el puerto tcp cuando registra un cambio de campus
        self.redis = redis_connector

        self.gender_detector = gender.Detector(case_sensitive=False)
        # Colas internas
        self._conn_queue = []   # para upserts en 'conexiones'
        self._mov_queue  = []   # para inserts en 'movimientos'
        # Task de flush periódico
        self._stop = asyncio.Event()
        self._flush_task =asyncio.create_task(self._flush_loop())
        
    @staticmethod
    def _parse_ts(s: str) -> datetime:
        # s == "Apr 21 13:50:39 2025"
        mon = DataProcessor._MONTHS[s[0:3]]
        day = int(s[4:6].strip())
        h, m, sec = map(int, s[7:15].split(':'))
        year = int(s[16:20])
        return datetime(year, mon, day, h, m, sec)
    def _normalizar_campus(self, campus_raw):
        """Normaliza los diferentes nombres de campus a las dos categorías principales"""
        # Mapeo de alias a nombres estandarizados
        campus_map = {
            'CC': 'CENTRAL',
            'Comisariato': 'CENTRAL',
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
            #obtener mensaje dentro de ahi tengo: ap(campus-ap),user(correo hash),timestamp
            data = json.loads(message)
            ap = data.get('ap', '') 
            user = data.get('user', '') 
            timestamp = data.get('timestamp', '')
            #Seperar la fecha y la hora
            ts_dt = self._parse_ts(timestamp)
            date   = ts_dt.strftime("%Y-%m-%d")
            time   = ts_dt.strftime("%H:%M:%S")
            # Separar el campus y el AP
            try:
                campus_raw, ap = ap.split('-', 1)
                campus = self._normalizar_campus(campus_raw)
            except Exception as e:
                logging.error(f"Error al dividir _ap_name_: {e}")
                return None
    
            # Con el correo, se procede a inferir en el genero y hashear el correo
            gender_inferred = self._infer_gender(user)
            #hash del correo
            correo_hash = hashlib.blake2s(user.encode("utf-8")).hexdigest()
            #se retorna un diccionario con los datos necesarios para guardar en la base de datos
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
    async def process_message(self, message):
        try:
            datos = await asyncio.to_thread(self._parse_message, message)
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
                await self.redis.cache_last_campus(correo, datos['campus'], ex=300)
            # Encolamos el upsert de conexión
            self._conn_queue.append((
                correo, datos['gender'], datos['ap'],
                datos['campus'], datos['fecha'], datos['hora']
            ))
            if not last: 
                await self.redis.cache_last_campus(correo, datos['campus'], ex=300)
        except Exception as e:
            logging.error(f"Error procesando mensaje: {e}")
    
    async def _flush_loop(self):
        try:
            while not self._stop.is_set():
            # Si la cola crece demasiado, flush inmediato          # intervalo de flush
                if len(self._conn_queue) >= 100000 or len(self._mov_queue) >= 100000:
                    await self._flush_batches()
                    continue
            # Si no, espera un segundo y luego flush
                await asyncio.sleep(1)
                await self._flush_batches()
        except asyncio.CancelledError:
            # última pasada antes de morir
            await self._flush_batches()
            raise
    
    async def _flush_batches(self):
        try:
            #logging.info(">> Entrando a _flush_batches()")
            BATCH_MAX = 200000
            batch = self._conn_queue[:BATCH_MAX]
            # Flush de conexiones
            if batch:
                await self.db_handler.bulk_upsert_conexiones(batch)
                del self._conn_queue[:len(batch)]
            # Flush de movimientos
            mov_batch = self._mov_queue[:BATCH_MAX]
            if mov_batch:
                await self.db_handler.bulk_insert_movimientos(mov_batch)
                del self._mov_queue[:len(mov_batch)]
        except Exception as e:
            logging.error(f"Error en _flush_batches: {e}")

    async def shutdown(self):
        #Llamar antes de salir para cerrar todo ordenadamente.
        self._stop.set()           # Indicamos que termine el while
        self._flush_task.cancel()  # Cancelamos la tarea de flush
        try:
            await self._flush_task
        except asyncio.CancelledError:
            pass
        await self._flush_batches()        # flush final
        await self.redis.close()           # método que ya tienes
        await self.db_handler.close()      # método que ya tienes
    @cached(_GENDER_CACHE)
    def _infer_gender(self, user: str) -> str:
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
    #Traemos el último campus del usuario desde Redis o BD    
    async def _get_last_campus_cached(self, correo_hash):
        #Traemos el valor del usuario con campus de cache en redis
        last = await self.redis.get_last_campus(correo_hash)
        if last:
            return last # Si está en cache, lo devolvemos y salimos de la función
        # Si no está en cache, lo traemos  el campus del usuario de BD
        last = await self.db_handler.get_last_campus(correo_hash)
        if last:
            # Guardamos el último campus del usuario en cache para futuras consultas
            # y le damos un tiempo de expiración de 5 minutos (300 segundos)
            await self.redis.cache_last_campus(correo_hash, last, ex=300)
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
            
            # orjson.dumps() ya devuelve bytes, así que añadimos un newline en bytes:
            payload = json.dumps(movement_data)  # bytes
            payload += b'\n'          
            # Enviar datos - para UDP no necesitamos conectar primero
            self.sock.sendto(payload, (self.target_host, self.target_port))
            #logging.info(f"Mensaje UDP enviado: {message}")
            #self.sock.close()
            
            logging.info(f"Notificación de movimiento enviada: {campus_anterior} → {campus_actual}")

            #logging.info(f"Se estan enviado los datos a : {self.target_host} → {self.target_port}")
        except Exception as e:
            logging.error(f"Error al enviar notificación de movimiento: {e}")

async def main():
    executor = ThreadPoolExecutor(max_workers=4)
    loop = asyncio.get_event_loop()
    loop.set_default_executor(executor)
    try:
        redis_connector = RedisConnector()
        await redis_connector.connect()

        db_handler = DBHandler()
        movement_notifier = MovementNotifier()  # Crear instancia
        data_processor = DataProcessor(db_handler,movement_notifier,redis_connector)
        
        stop_event = asyncio.Event()

        logging.info("Iniciando procesador de mensajes...")
        def _graceful(_sig, _frm):
            stop_event.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _graceful)
        while not stop_event.is_set():
            message = await redis_connector.get_message("socket_messages", timeout=1)
            if message:
                await data_processor.process_message(message)
        await data_processor.shutdown()
                
    except KeyboardInterrupt:
        logging.info("Proceso interrumpido por el usuario.")
    except Exception as e:
        logging.error(f"Error en el procesamiento principal: {e}")
    finally:
        executor.shutdown(wait=False)

if __name__ == "__main__":
    asyncio.run(main())
