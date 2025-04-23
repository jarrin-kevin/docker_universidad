from abc import ABC, abstractmethod
import redis
import json
import asyncio
import logging
import gzip
import subprocess
from datetime import datetime,timedelta
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
    def login_info(self): 
       #configura el modulo de loggin para registrar mensajes
       logging.basicConfig( 
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
        )  
       logging.info("UDP server starting...")     
    
    async def verificar_redis(self):
        """Verifica si Redis está corriendo antes de iniciar el servidor."""
        try:
            self.redis_client = redis.from_url(self.redis_url)
            self.redis_client.ping()
            #logging.info("Conexión con Redis exitosa.")
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
                    syslog_result = self.process_syslog_message(decoded_message)
                    if syslog_result:
                        # Si se procesó correctamente como syslog, termina
                        return
                except Exception as e:
                    logging.error(f"Error procesando mensaje: {e}")
        except Exception as e:
            logging.error(f"Error en la conexión con {client_address}: {e}")

    def process_syslog_message(self, message):
        """
        Procesa mensajes con formato syslog (que han sido filtrados por rsyslog-proxy)
        y los convierte a formato JSON para almacenarlos en Redis.
    
        Formato syslog esperado: <prioridad> fecha host mensaje
        """
        try:
            # Verificar que el mensaje tenga el formato esperado
            logging.info("Procesando mensaje syslog tiene, verificar si tiene correo")
            #email_match = re.search(r'(?:user(?:name)?[-:\s]+|username[-:\s]+)([\w.]+@[\w.]+)', message) antiguo buscador de correo
            email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', message)
            if not email_match:
                logging.warning("Mensaje descartado: no contiene patrón de correo electrónico")
                return True  # Retornamos True para indicar que se procesó (aunque se descartó)
            
            # Crear un objeto JSON con el formato que espera processor
            json_message = {
                "message": message  # El mensaje completo - processor extrae la info con regex
            }
            # Convertir a string JSON
            json_str = json.dumps(json_message)
            #logging.info(f"Mensaje syslog convertido a JSON: {json_str[:200]}...")

            # Enviar a Redis
            asyncio.create_task(
                asyncio.to_thread(self.redis_client.rpush, "socket_messages", json_str)
            )
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
        logging.info(f"Datagrama recibido de {addr}, tamaño: {len(data)} bytes")
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

