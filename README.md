## Sinchan Paper Trading Bot

A colorful, Sinchan-inspired paper trading bot with a simple SMA crossover strategy.

### Local Development

```bash
python -m pip install -r requirements.txt
python app.py
```

The app prints a localhost URL with a **random available port**. Open it in your browser.

### Deploy to Render

1. **Push to GitHub** (if not already):
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

2. **Go to Render Dashboard**:
   - Visit https://dashboard.render.com
   - Sign up/login (free tier available)

3. **Create New Web Service**:
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Render will auto-detect `render.yaml` configuration

4. **Deploy**:
   - Click "Create Web Service"
   - Render builds and deploys automatically

Your app will be live at `https://your-app-name.onrender.com`

**Note:** See `DEPLOY.md` for detailed instructions.

The app automatically uses Render's PORT environment variable when deployed.

### Notes

- Paper trading only (simulated prices).
- Strategy: simple SMA crossover.
- Symbols: SHINCHAN, KAZAMA, MASAO, BOCHAN, NENE

