[Unit]
Description=Coo Monster Panel - web admin and Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__APP_USER__
Group=__APP_GROUP__
WorkingDirectory=__APP_DIR__
Environment=VPN_HOST=127.0.0.1
Environment=VPN_PORT=__VPN_PORT__
ExecStart=__PY__ __APP_DIR__/app.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=__APP_DIR__

[Install]
WantedBy=multi-user.target
