# Paper Trading BTC - Estrategia #2233

Archivos para Railway.

Estrategia:
- RSI_REVERSION LONG
- Entrada: RSI(10) cruza hacia abajo 35.03
- Salida: RSI(10) > 73.93
- Stop Loss: 6.6%
- Take Profit: 19.0%
- Comisión simulada: 0.10% por lado
- Velas: 1 hora
- No envía órdenes reales

Para persistir los CSV/JSON en Railway, conecta un Volume al servicio.
El script usa automáticamente la variable RAILWAY_VOLUME_MOUNT_PATH.
Una ruta simple es /data.
