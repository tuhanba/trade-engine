#!/bin/bash
# AURVEX Ai - Production Deploy Script
set -e

DIR="/root/trade_engine"

echo "🚀 Deploying AURVEX Ai..."
cd $DIR

echo "📦 Pulling latest changes..."
git pull origin main

echo "🐍 Updating dependencies..."
source .venv/bin/activate
pip install -r requirements.txt

echo "⚙️ Restarting services..."
systemctl daemon-reload
systemctl restart ax-bot
systemctl restart ax-dashboard

echo "✅ Deployment successful!"
systemctl status ax-bot -l --no-pager | head -n 10
systemctl status ax-dashboard -l --no-pager | head -n 10
