# Render Deployment Guide

## Prerequisites

1. **GitHub Account** - Render deploys from GitHub repositories
2. **Render Account** - Sign up at https://render.com (free tier available)

## Deployment Steps

### Option 1: Deploy via Render Dashboard (Recommended)

1. **Push your code to GitHub:**
   ```bash
   git init
   git add .
   git commit -m "Initial commit: Sinchan Paper Trading Bot"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

2. **Go to Render Dashboard:**
   - Visit https://dashboard.render.com
   - Click "New +" → "Web Service"

3. **Connect your GitHub repository:**
   - Select your repository
   - Render will auto-detect the `render.yaml` configuration

4. **Configure (or use render.yaml defaults):**
   - **Name:** sinchan-trading-bot (or your choice)
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python app.py`
   - **Plan:** Free (or choose a paid plan)

5. **Deploy:**
   - Click "Create Web Service"
   - Render will build and deploy automatically

### Option 2: Deploy via Render CLI

```bash
# Install Render CLI (if you want CLI deployment)
npm install -g render-cli

# Login
render login

# Deploy (if using CLI)
render deploy
```

## After Deployment

Your app will be live at: `https://sinchan-trading-bot.onrender.com` (or your chosen name)

**Note:** Free tier services spin down after 15 minutes of inactivity. First request after spin-down may take 30-60 seconds.

## Configuration Files

- `render.yaml` - Render service configuration (auto-detected)
- `requirements.txt` - Python dependencies
- `runtime.txt` - Python version specification

## Troubleshooting

- **Build fails:** Check `requirements.txt` and `runtime.txt`
- **App won't start:** Check logs in Render dashboard
- **Port errors:** The app automatically uses Render's PORT env var
- **Slow first load:** Free tier spins down after inactivity - this is normal

## View Logs

- Go to your service in Render dashboard
- Click "Logs" tab to see real-time logs
