# Lumi loves to run:

https://github.com/user-attachments/assets/9d50facd-fc38-4fd6-8d27-cc8fcf8d2b0d

But I have often wondered, how fast does he run? 

With the power of a Raspberry Pi + magnets + InfluxDB + Grafana, now I can know exactly how fast and far each run is! 

<img width="2243" height="1013" alt="Screenshot 2025-08-21 172513" src="https://github.com/user-attachments/assets/3ff5e46e-92a6-488e-9114-a68280e5b4d3" />

I can also see aggregated data over time, and find out cumulative distance, max, average speed over a specific time window. 

<img width="2250" height="1089" alt="Screenshot 2025-08-21 172426" src="https://github.com/user-attachments/assets/8d1db0a9-6ac2-425a-b0bc-4dfc7690ca12" />

Uses a hall effect sensor + magnets mounted around the circumference of the wheel to gather info on speed over time. Logs velocity measurements at up to 10Hz and logically groups individual runs in a locally hosted InfluxDB database. 

# #todo:
- RGB Matrix speed display, so camera can see the MPH output
- Telegram notifications, to send a snapshot of the graph when events are captured
- AI Cat identifier, to characterize which cat is running based on the pattern (Miso is not as elegant)
