from abc import ABC, abstractmethod
import redis
import json
import asyncio
import logging
import subprocess
from config import CONFIG_RECEIVER

class DataReceiver(ABC): #Clase recibir los datos 
    @abstractmethod
    async def receiver_data(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Método para recibir datos desde un puerto."""
        pass
# Definición de excepción personalizada para errores con Redis
class RedisNotAvailableError(Exception):
    """Excepción lanzada cuando Redis no está disponible o no se pudo iniciar."""
    pass

class receiver_socket(DataReceiver):
    def __init__(self,host,port,redis_url):
        self.host = host #esto instancia en archivo de configuracion
        self.port = port#esto instancia en archivo de configuracion
        self.redis_url = redis_url#esto instancia en archivo de configuracion
    def login_info(self): 
       #configura el modulo de loggin para registrar mensajes
       logging.basicConfig( 
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
        )  
       logging.info("Socket server starting...")      

    async def verificar_redis(self):
        """Verifica si Redis está corriendo antes de iniciar el servidor."""
        try:
            self.redis_client = redis.from_url(self.redis_url)
            self.redis_client.ping()
            logging.info("Conexión con Redis exitosa.")
        except Exception as e:
            logging.error(f"Error conectando a Redis: {e}")
            # Se lanza la excepción personalizada para que el orquestador la capture
            raise RedisNotAvailableError("Redis no está disponible y no se pudo iniciar.") from e

    
    
    async def inicializar_servidor (self):
        #Esta función inicializa y ejecuta el servidor TCP utilizando asyncio.
        await self.verificar_redis() #Inicializa redis
        self.server = await asyncio.start_server(self.receiver_data, self.host, self.port) # elf.handle_client: Es una referencia al método siguiente
        logging.info(f"Escuchando en {self.host}:{self.port}...")
        async with self.server: #Contexto que asegura que el servidor se cierre adecuadamente si ocurre algún error.
            await self.server.serve_forever() #Mantiene el servidor activo y en espera de nuevas conexiones de forma indefinida.



    async def receiver_data(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_address = writer.get_extra_info("peername") #Obtiene información sobre el cliente conectado, tupla (host, port)
        logging.info(f"Conexión establecida desde: {client_address}")
       # El uso de await permite que otras tareas sigan ejecutándose mientras se realiza la operación.
        buffer = b""  # Buffer para acumular datos si llegan fragmentados
        #Maneja una conexión individual con un cliente, incluyendo la recepción de datos y su procesamiento.
        try:
            while True: #(mantiene la conexión mientras el cliente siga enviando dato)
                data = await reader.read(4096)#lee los datos que llegan en fragmentos.
                if not data:# si no hay data rompe la conexion
                    break
                buffer += data #va guardando en buffer
                # Para visualizar los datos en consola
                logging.info(f"Datos recibidos: {data.decode('utf-8', errors='ignore')}")  # Imprime los datos en formato legible
                messages = buffer.split(b'\x00') # hasta encontrar el separador \x00 separa los mensajes
                # Procesar mensajes completos
                for message in messages[:-1]: #Selecciona todos los elementos menos el último. Razón: Si el último mensaje no está completo (falta más información), no se puede procesar, Se guarda en el buffer para completar la información en la próxima iteración.
                    #decodificar el mensaje
                    decoded_message = message.decode("utf-8")
                    if self.is_valid_message(decoded_message):
                        # Enviar mensaje a la cola de Redis
                        await asyncio.to_thread(self.redis_client.rpush, "socket_messages", decoded_message)
                    #self.redis_client.rpush("socket_messages", decoded_message) #Inserta el mensaje decodificado al final de una lista en Redis llamada socket_messages
                    else:
                        logging.warning("Mensaje inválido descartado en receiver.")
                buffer = messages[-1] # Si el último mensaje está incompleto, se guarda en el buffer para ser completado más adelante.
                
        except Exception as e:
            print(f"Error en la conexión con {client_address}: {e}")
        finally:
            logging.info(f"Cerrando conexión con {client_address}")
            writer.close() #Cierra la conexión con el cliente de forma ordenada.
            await writer.wait_closed()
    
    def is_valid_message(self, message: str) -> bool:
        try:
            data = json.loads(message)
            # Verificar que existan y contengan valor los campos obligatorios
            required_fields = ["_ap_name", "_user", "_timestamp"]
            return all(field in data and data[field] for field in required_fields)
        except json.JSONDecodeError:
            return False


if __name__ == "__main__":
    try:
        receiver = receiver_socket(
            CONFIG_RECEIVER["host"],
            CONFIG_RECEIVER["port"],
            CONFIG_RECEIVER["redis_url"]
        )
        receiver.login_info()
        asyncio.run(receiver.inicializar_servidor())
    except RedisNotAvailableError as e:
        print(f"Error crítico: {e}")
        exit(1)