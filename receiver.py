from abc import ABC, abstractmethod
import redis.asyncio as aioredis
import orjson as json
import asyncio
import logging
import re
from config import CONFIG_RECEIVER

class DataReceiver(ABC): #Clase recibir los datos 
    @abstractmethod
    #async def receiver_data(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    async def receiver_data(self, data: bytes, addr):
        """Método para recibir datos desde un puerto."""
        pass

# Definición de excepción personalizada para errores con Redis
class RedisNotAvailableError(Exception):
    """Excepción lanzada cuando Redis no está disponible o no se pudo iniciar."""
    pass

class receiver_udp(DataReceiver):
    def __init__(self, host, port, redis_url):
        self.host = host #esto instancia en archivo de configuracion
        self.port = port #esto instancia en archivo de configuracion
        self.redis_url = redis_url #esto instancia en archivo de configuracion
        #COntadores
        self._msg_counter  = 0
        self._log_interval = 100
        #Expresiones regulares para buscar patrones en los mensajes
        self.ap_pattern    = re.compile(r'\bAP:(?P<ap>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\b')
        self.email_pattern = re.compile(r'\b(?:user(?:name)?-)?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})')
        self.ts_pattern    = re.compile(r'timestamp="([^"]+)"')
    def login_info(self): 
       #configura el modulo de loggin para registrar mensajes
       logging.basicConfig( 
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
        )  
       #logging.info("UDP server starting...")     
    
    async def verificar_redis(self):
        """Verifica si Redis está corriendo antes de iniciar el servidor."""
        try:
            self.redis_client = aioredis.from_url(self.redis_url)
            await self.redis_client.ping()
            logging.info("Conexión con Redis exitosa.")
        except Exception as e:
            logging.error(f"Error conectando a Redis: {e}")
            # Se lanza la excepción personalizada para que el orquestador la capture
            raise RedisNotAvailableError("Redis no está disponible y no se pudo iniciar.") from e 
    

    async def inicializar_servidor(self):
        # Esta función inicializa y ejecuta el servidor UDP utilizando asyncio.
        await self.verificar_redis() # Inicializa redis           

        # Crear el socket UDP y enlazarlo al puerto especificado
        self.transport, self.protocol = await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: UDPServerProtocol(self),
            local_addr=(self.host, self.port)
        )
        
        logging.info(f"Escuchando en {self.host}:{self.port} (UDP)...")
        
        # Mantener el servidor activo
        try:
            while True:
                await asyncio.sleep(360)  # Esperar indefinidamente
        except asyncio.CancelledError:
            self.transport.close()
            logging.info("Servidor UDP detenido.")


    async def receiver_data(self, data, addr):
        msg = data.decode('utf-8', errors='ignore').strip()
        if msg == 'ping':
            self.transport.sendto(b'pong', addr)
            return  # no procesar nada más
        client_address = addr
        try:
            # En UDP los datagramas llegan completos, no hay concepto de "fragmentos"
            #logging.info(f"Datos recibidos: {len(data)} bytes")
            
            # Procesar el mensaje
            if data:
                try:
                    decoded_message = data.decode("utf-8", errors='ignore')
                    # Intenta procesar como syslog primero
                    logging.info(f"MENSAJE SYSLOG: {decoded_message}")
                    self._msg_counter += 1
                    if self._msg_counter % self._log_interval == 0:
                        logging.info(f"Procesados {self._msg_counter} mensajes desde el arranque")
                    
                    asyncio.create_task(self.process_syslog_message(decoded_message))
                except Exception as e:
                    logging.error(f"Error procesando mensaje: {e}")
        except Exception as e:
            logging.error(f"Error en la conexión con {client_address}: {e}")

    async def process_syslog_message(self, message):
        """
        Procesa mensajes con formato syslog (que han sido filtrados por rsyslog-proxy)
        y los convierte a formato JSON para almacenarlos en Redis tomando solo las partes necesarias y tambien busca si hay mensajes repetidos.
        """
        try:
            email_match = self.email_pattern.search(message)
            if not email_match:
                logging.warning("Mensaje descartado: no contiene patrón de correo electrónico")
                return True
            user = email_match.group(1)
            logging.info(f"Usuario encontrado: {user}")

            ap_match = self.ap_pattern.search(message)
            if not ap_match:
                logging.warning("No se encontró AP en: {message}")
                return None  
            ap = ap_match.group(1)
            logging.info(f"AP encontrado: {ap}")

            ts_match = self.ts_pattern.search(message)
            if not ts_match:
                logging.error(f"No se encontró timestamp en: {message}")
                return None
            timestamp = ts_match.group(1)

            logging.info(f"Timestamp encontrado: {timestamp}")
            # Clave de deduplicado: combina timestamp, AP y usuario
            dup_key = f"dup:{timestamp}|{ap}|{user}"
            # Intentar marcar como nuevo en Redis (SET NX EX 300s)
            is_new = await self.redis_client.set(dup_key, 1, nx=True, ex=120)
            if not is_new:
                logging.info("Mensaje duplicado descartado.")
                return True  # Mensaje duplicado, no se procesa
            
            # Crear un objeto JSON con el formato que espera processor
            json_message = {
                "user": user,  
                "ap": ap,
                "timestamp": timestamp,
            }
            # Convertir a string JSON
            json_str = json.dumps(json_message)
            # Enviar a Redis
            #asyncio.create_task(
            #    asyncio.to_thread(self.redis_client.rpush, "socket_messages", json_str)
            #)
            try:
                await self.redis_client.rpush("socket_messages", json_str)
                #logging.info("PUSH a Redis: %s")
            except Exception as e:
                logging.error("❌  Error enviando a Redis")
            logging.info("Mensaje syslog procesado y enviado a Redis")
            return True
        except Exception as e:
            logging.error(f"Error procesando mensaje syslog: {e}")
            return False


# Clase para manejar el protocolo UDP
class UDPServerProtocol:
    def __init__(self, receiver):
        self.receiver = receiver
        
    def connection_made(self, transport):
        pass  # No es necesario hacer nada especial cuando se crea la conexión UDP
        
    def datagram_received(self, data, addr):
        # Este método se llama cada vez que se recibe un datagrama UDP
        #logging.info(f"Datagrama recibido de {addr}, tamaño: {len(data)} bytes")
        asyncio.create_task(self.receiver.receiver_data(data, addr))

    def error_received(self, exc):
        logging.error(f"Error en el protocolo UDP: {exc}")
        
    def connection_lost(self, exc):
        logging.info("Conexión UDP cerrada")


if __name__ == "__main__":
    try:
        receiver = receiver_udp(
            CONFIG_RECEIVER["host"],
            CONFIG_RECEIVER["port"],
            CONFIG_RECEIVER["redis_url"]
        )
        receiver.login_info()
        asyncio.run(receiver.inicializar_servidor())
    except RedisNotAvailableError as e:
        print(f"Error crítico: {e}")
        exit(1)

