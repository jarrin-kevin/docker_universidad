 
# Clase modelo para crear tablas
class BaseTable: 
    """Clase base para definir un modelo de tabla."""
    def __init__(self, name, create_sql):
        self.name = name # Nombre de la tabla
        self.create_sql = create_sql #Variable que va toma el valor de la query para crear la tabla (proviene de las clases modelos para crear la tabla)

    def create_table(self, cursor):# Metodo para ejecutar la query de creacion de tabla
        """Ejecuta la sentencia SQL para crear la tabla."""
        try:
            cursor.execute(self.create_sql) #ejecutar la query
            print(f"Tabla '{self.name}' creada o ya existente.")# mensaje informativo con la tabla si se creo
        except Exception as err:
            print(f"Error creando tabla '{self.name}': {err}") # error que puede suceder al crear la tabla
            raise

# Estrucutra de la tabla conexion
class ConexionTable(BaseTable):
    """Modelo para la tabla 'conexiones'."""
    def __init__(self):# Constructor donde se define como es la tabla junto con la query correspondiente para crearla
        create_sql = """ 
        CREATE TABLE IF NOT EXISTS conexiones (
            id_conexiones INT AUTO_INCREMENT PRIMARY KEY,
            correo VARCHAR(255) NOT NULL,
            sexo VARCHAR(255) NOT NULL,
            ap VARCHAR(255) NOT NULL,
            campus VARCHAR(255) NOT NULL,
            fecha DATE NOT NULL,
            hora TIME NOT NULL,
            INDEX(correo)
        );
        """
        # Llama a la clase padre con el constructor donde le pase el nombre de la tabla, junto con la query para crear la tabla
        super().__init__("conexiones", create_sql)
# Estrucutra de la tabla movimiento
class MovimientoTable(BaseTable):
    """Modelo para la tabla 'movimientos'."""
    def __init__(self):# Constructor donde se define como es la tabla junto con la query correspondiente para crearla
        create_sql = """
        CREATE TABLE IF NOT EXISTS movimientos (
            id_movimientos INT AUTO_INCREMENT PRIMARY KEY,
            correo VARCHAR(255) NOT NULL,
            sexo VARCHAR(255) NOT NULL,
            campus_anterior VARCHAR(255) NOT NULL,
            campus_actual VARCHAR(255) NOT NULL,
            fecha DATE NOT NULL,
            hora_llegada TIME NOT NULL,
            hora_salida TIME NOT NULL,
            INDEX(correo)
        );
        """
        # Llama a la clase padre con el constructor donde le pase el nombre de la tabla, junto con la query para crear la tabla
        super().__init__("movimientos", create_sql)

class EventoLimpiarConexiones(BaseTable):
    """Modelo para crear evento de limpieza diaria."""
    def __init__(self):
        create_sql = """
        CREATE EVENT IF NOT EXISTS limpiar_conexiones
        ON SCHEDULE EVERY 1 DAY
        STARTS CURRENT_DATE + INTERVAL 1 DAY
        DO
          TRUNCATE TABLE conexiones;
        """
        super().__init__("evento_limpiar", create_sql)