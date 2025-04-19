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

"""
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
        try:
            while True: #(mantiene la conexión mientras el cliente siga enviando dato)
                data = await reader.read(4096)#lee los datos que llegan en fragmentos.
                if not data:# si no hay data rompe la conexion
                    break
                buffer += data #va guardando en buffer
                # Para visualizar los datos en consola
                logging.info(f"Datos recibidos: {len(data)} bytes")

                # Procesar el mensaje una vez que tenemos todos los datos
                if buffer:
                    logging.info(f"Total de datos recibidos: {len(buffer)} bytes")
                    try:
                        decoded_message = buffer.decode("utf-8", errors='ignore')
                        logging.info(f"Mensaje decodificado (primeros 200 caracteres): {decoded_message[:200]}...")
                        valid, clean_message = self.is_valid_message(decoded_message)
                        if valid:
                        # Enviar mensaje a la cola de Redis
                            logging.info("Mensaje válido, enviando a Redis")
                            await asyncio.to_thread(self.redis_client.rpush, "socket_messages", clean_message)
                            buffer = b""  # Limpiar el buffer después de procesar con éxito
                        else:
                            logging.warning("Mensaje inválido descartado en receiver.")
                            buffer = b""  # También podría ser útil limpiar el buffer aquí
                
                    except Exception as e:
                        logging.error(f"Error procesando mensaje: {e}")
        except Exception as e:
            logging.error(f"Error en la conexión con {client_address}: {e}")
        

        finally:
            logging.info(f"Cerrando conexión con {client_address}")
            writer.close() #Cierra la conexión con el cliente de forma ordenada.
            await writer.wait_closed()
    
    def is_valid_message(self, message: str) -> bool:
        try:
            logging.info(f"Mensaje completo recibido: {message}")
            # Intenta encontrar los límites del objeto JSON
            # Busca desde el primer '{' hasta el último '}'
            start = message.find('{')
            end = message.rfind('}') + 1
            if start >= 0 and end > start:
                # Extrae solo el objeto JSON
                json_str = message[start:end]
                # Intenta analizarlo
                data = json.loads(json_str)
                # Muestra todos los campos para depuración
                logging.info(f"Campos en el mensaje JSON: {list(data.keys())}")
                # Verificar que existan y contengan valor los campos obligatorios
                timestamp_present = 'timestamp' in data or '_timestamp' in data
                message_present = (
                'message' in data or 
                'short_message' in data or 
                '_message' in data
                )
                if not 'message' in data:
                   if '_message' in data: 
                    data['message'] = data['_message']
                if not 'timestamp' in data and '_timestamp' in data:
                    data['timestamp'] = data['_timestamp']
                # Ahora verificar que existan y contengan valor
                required_fields = ["message", "timestamp"]

                result = all(field in data and data[field] for field in required_fields)
                if not result:
                    missing = [field for field in required_fields if field not in data or not data[field]]
                    logging.warning(f"Campos faltantes o vacíos: {missing}")
                else:
                    logging.info("Todos los campos requeridos están presentes y no vacíos")
                    # Si es válido, añade el mensaje limpio al buffer
                    return True, json_str
                return False, None
            logging.warning("No se encontró un objeto JSON válido en el mensaje")
            return False, None
        except json.JSONDecodeError as e:
            logging.warning(f"Error decodificando JSON: {e}, mensaje: {message[:100]}...")
            return False, None
        except Exception as e: 
            logging.error(f"Error inesperado en validación: {e}")
            return False, None
"""

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
        client_address = addr
        logging.info(f"Datos recibidos desde: {client_address}")
        
        try:
            # En UDP los datagramas llegan completos, no hay concepto de "fragmentos"
            logging.info(f"Datos recibidos: {len(data)} bytes")
            
            # Procesar el mensaje
            if data:
                try:
                    decoded_message = data.decode("utf-8", errors='ignore')
                    # Intenta procesar como syslog primero
                    logging.info(f"MENSAJE FILTRADO COMPLETO: {decoded_message}")
                    syslog_result = self.process_syslog_message(decoded_message)
                    if syslog_result:
                        # Si se procesó correctamente como syslog, termina
                        return
                    # Si no es syslog, intenta procesar como JSON
                    valid, clean_message = self.is_valid_message(decoded_message)
                    if valid:
                        # Enviar mensaje a la cola de Redis
                        logging.info("Mensaje válido, enviando a Redis")
                        await asyncio.to_thread(self.redis_client.rpush, "socket_messages", clean_message)
                    else:
                        logging.warning("Mensaje inválido descartado en receiver.")
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
            logging.info("Procesando mensaje syslog filtrado, si tiene correo")
            email_match = re.search(r'(?:user(?:name)?[-:\s]+|username[-:\s]+)([\w.]+@[\w.]+)', message)
            if not email_match:
                logging.warning("Mensaje descartado: no contiene patrón de correo electrónico")
                return True  # Retornamos True para indicar que se procesó (aunque se descartó)
            
            # Crear un objeto JSON con el formato que espera processor
            json_message = {
                "timestamp": datetime.now().isoformat() + "Z",
                "message": message  # El mensaje completo - processor extrae la info con regex
            }
            # Convertir a string JSON
            json_str = json.dumps(json_message)
            logging.info(f"Mensaje syslog convertido a JSON: {json_str[:200]}...")

            # Enviar a Redis
            asyncio.create_task(
                asyncio.to_thread(self.redis_client.rpush, "socket_messages", json_str)
            )
            logging.info("Mensaje syslog procesado y enviado a Redis")
            return True
        except Exception as e:
            logging.error(f"Error procesando mensaje syslog: {e}")
            return False


    def is_valid_message(self, message: str) -> tuple:
        try:
            logging.info(f"Mensaje completo recibido: {message}")
            # Intenta encontrar los límites del objeto JSON
            # Busca desde el primer '{' hasta el último '}'
            start = message.find('{')
            end = message.rfind('}') + 1
            if start >= 0 and end > start:
                # Extrae solo el objeto JSON
                json_str = message[start:end]
                # Intenta analizarlo
                data = json.loads(json_str)
                # Muestra todos los campos para depuración
                logging.info(f"Campos en el mensaje JSON: {list(data.keys())}")
                # Verificar que existan y contengan valor los campos obligatorios
                timestamp_present = 'timestamp' in data or '_timestamp' in data
                message_present = (
                'message' in data or 
                'short_message' in data or 
                '_message' in data
                )
                if not 'message' in data:
                   if '_message' in data: 
                    data['message'] = data['_message']
                if not 'timestamp' in data and '_timestamp' in data:
                    data['timestamp'] = data['_timestamp']
                # Ahora verificar que existan y contengan valor
                required_fields = ["message", "timestamp"]

                result = all(field in data and data[field] for field in required_fields)
                if not result:
                    missing = [field for field in required_fields if field not in data or not data[field]]
                    logging.warning(f"Campos faltantes o vacíos: {missing}")
                else:
                    logging.info("Todos los campos requeridos están presentes y no vacíos")
                    # Si es válido, añade el mensaje limpio al buffer
                    return True, json_str
                return False, None
            logging.warning("No se encontró un objeto JSON válido en el mensaje")
            return False, None
        except json.JSONDecodeError as e:
            logging.warning(f"Error decodificando JSON: {e}, mensaje: {message[:100]}...")
            return False, None
        except Exception as e: 
            logging.error(f"Error inesperado en validación: {e}")
            return False, None

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



    #try:
    #    receiver = receiver_socket(
    #        CONFIG_RECEIVER["host"],
    #        CONFIG_RECEIVER["port"],
    #        CONFIG_RECEIVER["redis_url"]
    #    )
    #    receiver.login_info()
    #    asyncio.run(receiver.inicializar_servidor())
    #except RedisNotAvailableError as e:
    #    print(f"Error crítico: {e}")
    #    exit(1)
