# Data Sources Registry

| Provider | Asset Class | Endpoint/Protocol | Real-time | WebSocket | API Key | Usage |
| -------- | ----------- | ----------------- | --------- | --------- | ------- | ----- |
| Binance  | CRYPTO      | REST/Websocket    | Yes       | Yes       | No (Pub)| Analysis/Trading |
| Yahoo Fin| FOREX       | REST              | No        | No        | No      | Analysis/Demo |
| Yahoo Fin| INDICES     | REST              | No        | No        | No      | Analysis/Demo |
| Yahoo Fin| COMMODITIES | REST              | No        | No        | No      | Analysis/Demo |
| ForexFact| CALENDAR    | REST (JSON)       | Yes       | No        | No      | Risk Filtering |
| ING Think | MACRO       | RSS               | Yes       | No        | No      | Analysis/Context|
| National  | GENERAL     | RSS (Configurable)| Yes       | No        | No      | Market Context  |
| PrimeXBT  | BROKER      | Manual/Display    | Yes       | No        | No      | Execution(Manual)|

## Documentation
- **Binance API**: [Official Documentation](https://binance-docs.github.io/apidocs/spot/en/)
- **Yahoo Finance**: Publicly available via `yfinance` library wrapper.

## Latency Policy
- **LIVE**: < 5s latency.
- **DELAYED**: 15min+ latency (standard for free market data).
- **STALE**: Data older than 2 minutes for Crypto or session-end for others.
