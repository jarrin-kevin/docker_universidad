import Redis from 'ioredis';
import dotenv from 'dotenv';
import { getGender as detectGender } from 'gender-detection-from-name';
import mysql  from 'mysql2/promise';
import crypto from 'node:crypto';
import dgram from 'dgram';
import pino from 'pino';
dotenv.config();                     // Carga las variables definidas en .env

/* --------------- sección global --------------- */
let redis,db,processor,notifier;                     //   ⟵ 1️⃣  la declaras arriba
const logger = pino({
  level: process.env.LOG_LEVEL || 'info',
  transport: process.env.NODE_ENV === 'development' && {
    target: 'pino-pretty', options: { translateTime: 'SYS:standard' }
  }
});
/* ───── estadísticas ───── */
let totalRx = 0, jsonFail = 0, parseFail = 0;
class RedisConnector {
    constructor () {
        /* 1. Construye la URL  igual que en Python
              redis://:<pwd>@<host>:<port>                          */
        const { REDIS_HOST, REDIS_PORT, REDIS_PASSWORD } = process.env;
        if (!REDIS_HOST || !REDIS_PORT) throw new Error('ℹ️  Falta REDIS_HOST o REDIS_PORT en .env');
        this.url = `redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}`;
        // 2. Crea cliente ioredis; esto NO abre todavía la conexión 
        this.client = new Redis(this.url, { lazyConnect: true });
        this.client.on('error', err => console.error('Redis ↯', err));
    }
    /* Abre la conexión (ioredis lo hace implícitamente al primer comando,
    pero así conservamos la semántica “connect()” de Python). */
    async connect () {
        await this.client.connect();     // resuelve cuando el handshake está OK
        logger.info({ url: this.url }, 'Redis conectado');
    }
    /** BLPOP bloqueante — equivalente a get_message() en Python.
    *  queue   : nombre de la lista (por defecto "socket_messages")
    *  timeout : segundos; 0 = espera indefinida (igual que aioredis)
    *  Devuelve el “element” (string) o null si expiró el timeout.           */
    async getMessage (queue = 'socket_messages', timeout = 0) {
        const reply = await this.client.blpop(queue, timeout);   // [key, value]
        return reply ? reply[1] : null;
    }
    /** Guarda el último campus con TTL  (equivalente a cache_last_campus). */
    async cacheLastCampus (correo, campus, ex = 300) {
        await this.client.set(`last_campus:${correo}`, campus, 'EX', ex);
    }
    async cacheLastConnectionTime (correo, hora, ex = 300) {
        await this.client.set(`last_hora:${correo}`, hora, 'EX', ex);
    }
    /** Devuelve el campus guardado o null (equivalente a get_last_campus). */
    async getLastCampus (correo) {
        return this.client.get(`last_campus:${correo}`);
    }
    async getLastConnectionTime (correo) {
        return this.client.get(`last_hora:${correo}`);
    }
     /** Cierra la conexión limpiamente (close() en Python). */
    async close () {
        await this.client.quit();
    }
}

class DBHandler {
    constructor () {
        const {
            MYSQL_HOST,
            MYSQL_PORT,
            MYSQL_APP_USER,
            MYSQL_APP_PASSWORD,
            MYSQL_DATABASE,
            MYSQL_POOL_LIMIT = 50          // opcional en .env
        } = process.env;
        if (!MYSQL_HOST || !MYSQL_DATABASE || !MYSQL_APP_USER) throw new Error('ℹ️  Falta configuración MySQL');
      /*  mysql2/promise crea un **pool** de conexiones;
          cada consulta pide un socket del pool --> no bloquea el event-loop. */
        this.pool = mysql.createPool({
            host : MYSQL_HOST,
            port : Number(MYSQL_PORT) || 3306,
            user : MYSQL_APP_USER,
            password : MYSQL_APP_PASSWORD,
            database : MYSQL_DATABASE,
            waitForConnections : true,
            connectionLimit    : Number(MYSQL_POOL_LIMIT),
            queueLimit         : 0
        });
  
      /* Preparadas como plantillas para los lotes ----------------------- */
      this.SQL_CONEX = `
        INSERT INTO conexiones (correo, sexo, ap, campus, fecha, hora)
        VALUES ?
        ON DUPLICATE KEY UPDATE
          ap     = VALUES(ap),
          campus = VALUES(campus),
          hora   = VALUES(hora)
      `;
      this.SQL_MOV = `
        INSERT INTO movimientos
          (correo, sexo, campus_anterior, campus_actual, fecha,hora_llegada,hora_salida)
        VALUES ?
      `;
    }
  
    /* ---------- batch INSERT / UPSERT --------------------------------- */
    async bulkUpsertConexiones (batch /* array de objs */) {
      /* mysql2 acepta un array de arrays para VALUES ? → [[v1,v2…],…]  */
      const rows = batch.map(o => [o.correo, o.sexo, o.ap, o.campus, o.fecha, o.hora]);
      await this.pool.query(this.SQL_CONEX, [rows]);
    }
  
    async bulkInsertMovimientos (batch) {
      const rows = batch.map(o => [
        o.correo, o.sexo, o.campusAnterior, o.campusActual, o.fecha, o.hora_llegada,o.hora_salida
      ]);
      await this.pool.query(this.SQL_MOV, [rows]);
    }
  
    /* ---------- consulta simple --------------------------------------- */
    async getLastConnectionTime (correo) {
        const [[row] = []] = await this.pool.query(
          `SELECT hora FROM conexiones WHERE correo = ? ORDER BY id_conexiones DESC LIMIT 1`,
          [correo]
        );
        return row ? row.hora : null;
    }
  /** Devuelve el último campus registrado del usuario */
    async getLastCampus (correo) {
        const [[row] = []] = await this.pool.query(
            `SELECT campus FROM conexiones WHERE correo = ? ORDER BY id_conexiones DESC LIMIT 1`,
            [correo]
        );
        return row ? row.campus : null;
    }


    async close () { await this.pool.end(); }
}

class DataProcessor {
    constructor(dbHandler, movementNotifier, redisConnector) {
      this.db = dbHandler;
      this.notifier = movementNotifier;
      this.redis = redisConnector;
  
      this.connQueue = [];
      this.movQueue = [];
      this.maxBatchSize = 2000; // nº msgs que llenan el lote
      this.flushIntervalMs = 1000;  // cada 1 segundo
      this._stopped = false;
  
      /* timer: flush cada X ms */
      this.flushTimer = setInterval(() => {
        this._flushBatches().catch(err =>
          console.error('Flush timer error → sigo vivo:', err)
        );
    },  this.flushIntervalMs);
  }
    _enqueue(queue, item) {
      queue.push(item);
      /* disparo por tamaño */
      if (queue.length >= this.maxBatchSize) {
        this._flushBatches().catch(err =>
          logger.warn({ err }, 'Flush timer error → sigo vivo')
        );
      }
    }
    _parseTs(rawTs) {
        const [mon, day, timeStr, year] = rawTs.split(' ');
        const [h, m, s] = timeStr.split(':').map(n => parseInt(n, 10));
        const months = { Jan:0, Feb:1, Mar:2, Apr:3, May:4, Jun:5, Jul:6, Aug:7, Sep:8, Oct:9, Nov:10, Dec:11 };
        return new Date(+year, months[mon], +day, h, m, s);
    } 
    normalizarCampus(raw) {
        const map = { CC:'CENTRAL', Comisariato:'CENTRAL', CBAL:'BALZAY', balzay:'BALZAY', Balzay:'BALZAY',CREDU: 'CENTRAL',credu: 'CENTRAL',Credu: 'CENTRAL' };
        return map[raw] || raw;
    }
    _parseMessage(raw) {
        let data;
        try {
          data = JSON.parse(raw);
        } catch (e) {
          jsonFail++;
          logger.debug({ err: e, raw: raw.slice(0, 200) }, 'JSON inválido');
          return null;
        }
        const user = data.user;
        const apName = data.ap;
        const timestamp = data.timestamp;
        const dt = this._parseTs(timestamp);
        const fecha = dt.toISOString().slice(0, 10);
        const hora = dt.toTimeString().split(' ')[0];
    
        let campusRaw, ap;
        const idx = apName.indexOf('-');
        if (idx !== -1) {
          campusRaw = apName.slice(0, idx);
          ap = apName.slice(idx + 1);
        } else {
          campusRaw = apName;
          ap = '';
        }
    
        const parsed = {
          emailHash: crypto.createHash('blake2s256').update(user).digest('hex'),
          gender: this._inferGender(user),
          ap,
          campus: this.normalizarCampus(campusRaw),
          fecha,
          hora,
        };
        return parsed;
    }
    async processMessage(raw) {
        // 1. Parseo y validación
        let msg;
        try {
            msg = this._parseMessage(raw);
            if (!msg){
              return;
            } 
          } catch {
            return; // JSON inválido
          }
          
        const key       = msg.emailHash;
        const campusNew = msg.campus;
        const fecha     = msg.fecha;
        const hora      = msg.hora;
        /* 2. Obtener campus y hora de la última conexión del cache
        (siempre actualizamos el cache después de encolar la conexión) */
        let [campusOld, horaOld] = await Promise.all([
            this.redis.getLastCampus(key),
            this.redis.getLastConnectionTime(key)
        ]);
        // 3. Si no estaba en cache → miramos en BD 
        if (campusOld == null || horaOld == null) {
          [campusOld, horaOld] = await Promise.all([
            this.db.getLastCampus(key),
            this.db.getLastConnectionTime(key)]);           
            if (campusOld) await this.redis.cacheLastCampus(key, campusOld, 300);
            if (horaOld)   this.redis.cacheLastConnectionTime(key, horaOld, 300);   
        }
        // Si el campus actual es diferente al último campus
        if (campusOld && campusOld !== campusNew && horaOld) {
            // 4.b Encolar movimiento con hora anterior
            this._enqueue(this.movQueue,{
              correo: key,
              sexo: msg.gender,
              campusAnterior: campusOld,
              campusActual: campusNew,
              fecha: fecha,
              hora_llegada: hora,
              hora_salida: horaOld
            });
            // 4.c Notificar por UDP si corresponde
            if (this.notifier.enabled) {
                await this.notifier.notifyMovement(key, fecha, hora,horaOld,campusNew, campusOld);
            }
            // 4.d Cachear sólo el nuevo campus (TTL 300 s)
            await this.redis.cacheLastCampus(key, campusNew, 300);

        }
        
        // Encolar conexión
        this._enqueue(this.connQueue,{
            correo: key,
            sexo: msg.gender,
            ap: msg.ap,
            campus: msg.campus,
            fecha: fecha,
            hora: hora
      });
      // Actualizar caches
      await Promise.all([
        this.redis.cacheLastCampus(key, campusNew, 300),
        this.redis.cacheLastConnectionTime(key, hora, 300)
      ]);
    }

    async _flushBatches() {
      //console.log(`🗄️  Flush: ${this.connQueue.length} conexiones · ${this.movQueue.length} movimientos`);
      /* CONEXIONES */
      if (this.connQueue.length) {
        const batch = this.connQueue.splice(0);      // vacía array
        try {
          await this.db.bulkUpsertConexiones(batch);
        } catch (err) {
          //console.error('Error bulkUpsertConexiones:', err);
          logger.error({ err }, 'Error bulkUpsertConexiones');
          /* si falla, re-inyecta para reintentar en el próximo flush */
          this.connQueue.unshift(...batch);
          throw err;
        }
      }
  
      /* MOVIMIENTOS (solo válidos) */
      if (this.movQueue.length) {
        const batch = this.movQueue.splice(0).filter(m => m.hora_salida);
        if (batch.length) {
          try {
            await this.db.bulkInsertMovimientos(batch);
          } catch (err) {
            //console.error('Error bulkInsertMovimientos:', err);
            logger.error({ err }, 'Error bulkInsertMovimientos');
            this.movQueue.unshift(...batch);
            throw err;
          }
        }
      }
    }
    
    _inferGender(user) {
        const local = user.split('@')[0];
        const name = local.split('.')[0];
        const gen = detectGender(name, 'es');
        if (gen === 'male') return 'hombre';
        if (gen === 'female') return 'mujer';
        return 'desconocido';
    }
    async shutdown() {
        clearInterval(this.flushTimer);
        await this._flushBatches();
        await this.redis.close();
        await this.db.close();
    }
}


class MovementNotifier {
    constructor() {
      const {
        NOTIFY_HOST = '127.0.0.1',
        NOTIFY_PORT = 12349
      } = process.env;
      this.host = NOTIFY_HOST;
      this.port = Number(NOTIFY_PORT);
      this.socket = dgram.createSocket('udp4');
      this.enabled = true;
    }
  
    
     // Envía un mensaje JSON por UDP con datos de movimiento
     // @param {string} correo
     // @param {string} fecha
     // @param {string} hora_llegada
     // @param {string} hora_salida
     // @param {string} campusActual
     // @param {string} campusAnterior
    
    async notifyMovement(correo, fecha, hora_llegada, hora_salida, campusActual, campusAnterior) {
      const payload = JSON.stringify({
        correo,
        campusAnterior,
        campusActual,
        fecha,
        hora_llegada,
        hora_salida
      });
      this.socket.send(payload, this.port, this.host, err => {
        if (err) console.error('UDP send error:', err);
      });
    }
  
    /**
     * Cierra el socket UDP
     */
    close() {
      this.socket.close();
    }
  }
        
/* -------------------------------------------------------------
   FUNCIÓN PRINCIPAL 
   ------------------------------------------------------------- */
async function main () {
    redis = new RedisConnector();
    db    = new DBHandler();
    await redis.connect();
    notifier = new MovementNotifier();
    processor = new DataProcessor(db, notifier, redis);
    console.log(' Conectado a Redis, esperando mensajes…');
  
    while (true) {
        //console.log('  Esperando mensaje…');
        const raw = await redis.getMessage('socket_messages', 1);   // timeout 1 s
        if (raw){
          totalRx++;
          logger.debug({ raw: raw.slice(0, 200) }, '📩 mensaje recibido');   // solo con LOG_LEVEL=debug
          //console.log('📩  Mensaje recibido:', raw.slice(0, 120));
          try {
            await processor.processMessage(raw);
          }
          catch (err) {
            logger.error({ err, raw: raw.slice(0, 200) }, '❌ error procesando mensaje');
            //console.error('Error procesando mensaje:', err);
            //console.error('  Error procesando mensaje:', err);
          }
            /* resumen cada 1 000 mensajes */
          if (totalRx % 100 === 0) {
            logger.info({
            totalRx, jsonFail, parseFail,
            connQueue: processor.connQueue.length,
            movQueue : processor.movQueue.length
            }, '⏱️  resumen procesador');
          }
        }
      }    
    }


// Arranca (y captura CTRL-C para cerrar Redis ordenadamente)
main().catch(console.error);
/* --------------------------- SHUTDOWN ------------------------------- */
process.on('SIGINT', async () => {
    console.log('Deteniendo procesador...');
    try {
      await processor.shutdown();
      notifier.close();
    } finally {
      process.exit(0);
    }
});