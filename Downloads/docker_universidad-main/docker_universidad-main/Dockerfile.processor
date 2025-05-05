FROM python:3.12.3-slim

# Crear usuario no privilegiado
RUN groupadd -r appuser && useradd -r -g appuser -s /bin/false appuser

# Actualizar e instalar dependencias
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      gcc libmariadb-dev libssl-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requirements y instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala cachetools junto a otras deps
RUN pip install --no-cache-dir cachetools
# Copiar código
COPY processor.py config.py ./

# Crear directorios para almacenamiento temporal
RUN chmod 644 config.py && chown appuser:appuser config.py && \
    mkdir -p /tmp/cache && \
    chown -R appuser:appuser /tmp/cache && \
    chmod 700 /tmp/cache


# Cambiar al usuario no privilegiado
USER appuser


# Ejecutar script
CMD ["sh", "-c", "python processor.py || sleep 3600"]



