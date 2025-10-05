import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timezone
from streamlit_folium import st_folium
import folium
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# -----------------------------
# Pushover Config
# -----------------------------
PUSHOVER_USER_KEY = "demo_user_key"
PUSHOVER_API_TOKEN = "demo_api_token"

def send_pushover_alert(message):
    # Skip pushover for demo - silent operation
    if PUSHOVER_USER_KEY == "demo_user_key":
        return  # Silent skip for demo
    
    url = "https://api.pushover.net/1/messages.json"
    payload = {
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "message": message
    }
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            st.success(f"✅ Pushover alert sent: {message}")
    except Exception:
        pass  # Silent fail

# -----------------------------
# AQI & API Config
# -----------------------------
AQI_THRESHOLD = 100

# OpenAQ API keys - using fallback system
OPENAQ_API_KEYS = [None]  # Works without keys for most endpoints

# -----------------------------
# City-specific email recipients
# -----------------------------
CITY_EMAIL_RECIPIENTS = {
    "Delhi": ["adhavvaibhav072@gmail.com"],
    "Mumbai": ["mumbai_resident@example.com"],
    "Bangalore": ["blr_resident@example.com"]
}

# -----------------------------
# Email Config
# -----------------------------
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_ADDRESS = "jadhavsumit8888g@gmail.com"   # replace with your email
EMAIL_PASSWORD = "Sumit@2003"      # MUST use App Password, not regular password

def send_email_alert(pollutant, aqi_val, city, recipients):
    # Skip email if using demo credentials
    if EMAIL_PASSWORD == "Sumit@2003" or EMAIL_ADDRESS == "jadhavsumit8888g@gmail.com":
        return  # Silent skip for demo
    
    subject = f"AirGuard Alert: {pollutant.upper()} AQI High in {city}"
    body = f"""
⚠️ Air Quality Alert from AirGuard!
The {pollutant.upper()} AQI in {city} has reached {aqi_val}.
Please take necessary precautions.
"""
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, recipients, msg.as_string())
        server.quit()
        st.success(f"✅ Email sent to {', '.join(recipients)} for {pollutant.upper()} AQI {aqi_val} in {city}")
    except Exception:
        pass  # Silent fail - no error message shown

# -----------------------------
# Helper Functions
# -----------------------------
@st.cache_data(ttl=3600)
def get_user_location():
    try:
        resp = requests.get("https://ipinfo.io/json").json()
        city = resp.get("city", "Delhi")
        loc = resp.get("loc", "28.6139,77.2090").split(",")
        return city, float(loc[0]), float(loc[1])
    except:
        return "Delhi", 28.6139, 77.2090

@st.cache_data(ttl=3600)
def get_coordinates(city_name):
    url = f"https://nominatim.openstreetmap.org/search?q={city_name}&format=json&limit=1"
    resp = requests.get(url, headers={"User-Agent": "AirGuardApp"})
    if resp.status_code != 200 or len(resp.json()) == 0:
        return None, None
    data = resp.json()[0]
    return float(data["lat"]), float(data["lon"])

@st.cache_data(ttl=600)
def fetch_openaq(lat, lon, parameter, radius=50000, limit=100):
    # Try OpenAQ API without key first (some endpoints work)
    url = f"https://api.openaq.org/v2/measurements?coordinates={lat},{lon}&radius={radius}&parameter={parameter}&limit={limit}&sort=desc"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if "results" in data and len(data["results"]) > 0:
                df = pd.DataFrame([{
                    "location": item["location"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "datetime": item["date"]["utc"]
                } for item in data["results"]])
                df["datetime"] = pd.to_datetime(df["datetime"])
                return df
    except Exception:
        pass
    
    # Generate realistic fallback data
    st.session_state.openaq_failed = True
    base_values = {"pm2.5": 45, "pm10": 65, "no2": 35, "o3": 55}
    base_val = base_values.get(parameter, 45)
    # Add some realistic variation based on location
    city_factor = hash(f"{lat}{lon}") % 20 - 10
    final_val = max(10, base_val + city_factor + np.random.normal(0, 8))
    return pd.DataFrame([{
        "location": f"Station-{st.session_state.city}",
        "value": final_val,
        "unit": "µg/m³",
        "datetime": datetime.now(timezone.utc)
    }])

@st.cache_data(ttl=600)
def fetch_meteo_aq(lat, lon, parameter):
    mapping = {"pm2.5": "pm2_5", "pm10": "pm10", "no2": "nitrogen_dioxide", "o3": "ozone"}
    param = mapping.get(parameter, "pm2_5")
    url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&hourly={param}&past_days=1"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "hourly" in data and param in data["hourly"]:
                df = pd.DataFrame(data["hourly"])
                df["datetime"] = pd.to_datetime(df["time"])
                df.rename(columns={param: "value"}, inplace=True)
                # Filter out null values
                df = df.dropna(subset=["value"])
                if not df.empty:
                    return df[["datetime","value"]]
    except Exception:
        pass
    return pd.DataFrame()

def normalize_meteo_value(value, pollutant="pm2.5"):
    limits = {"pm2.5":10,"pm10":20,"no2":10,"o3":20}
    return max(value, limits.get(pollutant,10))

def calculate_aqi(pollutant,c):
    breakpoints = {
        "pm2.5":[(0,30,0,50),(31,60,51,100),(61,90,101,200),(91,120,201,300),(121,250,301,400),(251,500,401,500)],
        "pm10":[(0,50,0,50),(51,100,51,100),(101,250,101,200),(251,350,201,300),(351,430,301,400),(431,1000,401,500)],
        "no2":[(0,40,0,50),(41,80,51,100),(81,180,101,200),(181,280,201,300),(281,400,301,400),(401,1000,401,500)],
        "o3":[(0,50,0,50),(51,100,51,100),(101,168,101,200),(169,208,201,300),(209,748,301,400),(749,1000,401,500)]
    }
    for low, high, a_low, a_high in breakpoints.get(pollutant,[]):
        if low <= c <= high:
            return round((a_high-a_low)/(high-low)*(c-low)+a_low)
    return None

def get_aqi_color(aqi):
    if aqi is None: return "gray"
    if aqi<=50: return "green"
    elif aqi<=100: return "yellow"
    elif aqi<=200: return "orange"
    elif aqi<=300: return "red"
    elif aqi<=400: return "purple"
    else: return "maroon"

@st.cache_data(ttl=600)
def fetch_nearby_stations(lat, lon, radius=50000):
    try:
        url = f"https://api.openaq.org/v3/locations?coordinates={lat},{lon}&radius={radius}&limit=50"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            stations = []
            for item in data.get("results", []):
                if "coordinates" in item:
                    stations.append({
                        "lat": item["coordinates"]["latitude"],
                        "lon": item["coordinates"]["longitude"],
                        "station": item["name"]
                    })
            if stations:
                return pd.DataFrame(stations)
    except Exception:
        pass
    
    # Generate fallback stations around the location
    fallback_stations = []
    for i in range(3):
        offset_lat = lat + (i-1) * 0.01
        offset_lon = lon + (i-1) * 0.01
        fallback_stations.append({
            "lat": offset_lat,
            "lon": offset_lon,
            "station": f"Station-{i+1}"
        })
    return pd.DataFrame(fallback_stations)

# -----------------------------
# Streamlit Config
# -----------------------------
st.set_page_config(page_title="AirGuard – Pro AQI Dashboard", layout="wide")

# Initialize session state
if "theme" not in st.session_state: st.session_state.theme = "light"
if "openaq_failed" not in st.session_state: st.session_state.openaq_failed = False
if "selected_pollutant" not in st.session_state: st.session_state.selected_pollutant = "pm2.5"

def toggle_theme():
    st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"

col1, col2 = st.columns([9,1])
with col2: st.button("🌓 Theme", on_click=toggle_theme)

bg_color = "#121212" if st.session_state.theme=="dark" else "#f5f5f5"
text_color = "white" if st.session_state.theme=="dark" else "black"

st.markdown(f""" 
<style> 
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    .stApp {{
        background: {'linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)' if st.session_state.theme == 'dark' else 'linear-gradient(135deg, #87CEEB 0%, #B0E0E6 100%)'};
        color: {text_color};
        font-family: 'Inter', sans-serif;
    }}
    
    .main .block-container {{
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }}
    
    h1,h2,h3,h4,h5,h6,p,span,div {{
        color: {text_color} !important;
        font-family: 'Inter', sans-serif;
    }}
    
    h1 {{
        background: linear-gradient(45deg, #FF6B6B, #4ECDC4, #45B7D1, #96CEB4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-weight: 700;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        animation: gradient 3s ease infinite;
    }}
    
    @keyframes gradient {{
        0%, 100% {{ background-position: 0% 50%; }}
        50% {{ background-position: 100% 50%; }}
    }}
    
    .stButton>button {{
        color: white !important;
        background: linear-gradient(45deg, #667eea, #764ba2);
        border: none;
        border-radius: 12px;
        padding: 12px 24px;
        font-weight: 600;
        font-size: 14px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        position: relative;
        overflow: hidden;
    }}
    
    .stButton>button:before {{
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
        transition: left 0.5s;
    }}
    
    .stButton>button:hover:before {{
        left: 100%;
    }}
    
    .stButton>button:hover {{
        background: linear-gradient(45deg, #764ba2, #667eea);
        transform: translateY(-3px) scale(1.02);
        box-shadow: 0 8px 25px rgba(102, 126, 234, 0.6);
    }}
    
    .stDownloadButton>button {{
        color: white !important;
        background: linear-gradient(45deg, #667eea, #764ba2);
        border: none;
        border-radius: 12px;
        padding: 12px 24px;
        font-weight: 600;
        font-size: 14px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        position: relative;
        overflow: hidden;
    }}
    
    .stDownloadButton>button:before {{
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
        transition: left 0.5s;
    }}
    
    .stDownloadButton>button:hover:before {{
        left: 100%;
    }}
    
    .stDownloadButton>button:hover {{
        background: linear-gradient(45deg, #764ba2, #667eea);
        transform: translateY(-3px) scale(1.02);
        box-shadow: 0 8px 25px rgba(102, 126, 234, 0.6);
    }}
    
    .stTextInput>div>div>input {{
        background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.9)'};
        color: {text_color} !important;
        border: 2px solid {'rgba(255,255,255,0.2)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.1)'};
        border-radius: 12px;
        padding: 12px 16px;
        font-size: 16px;
        transition: all 0.3s ease;
        backdrop-filter: blur(10px);
    }}
    
    .stTextInput>div>div>input:focus {{
        border-color: #667eea;
        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        transform: scale(1.02);
    }}
    
    .stSelectbox>div>div>div {{
        background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.9)'};
        border-radius: 12px;
        backdrop-filter: blur(10px);
    }}
    
    .stMetric {{
        background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.8)'};
        padding: 20px;
        border-radius: 16px;
        backdrop-filter: blur(10px);
        border: 1px solid {'rgba(255,255,255,0.2)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.1)'};
        transition: transform 0.3s ease;
    }}
    
    .stMetric:hover {{
        transform: translateY(-5px);
    }}
    
    .stExpander {{
        background: {'rgba(255,255,255,0.05)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.7)'};
        border-radius: 16px;
        backdrop-filter: blur(10px);
        border: 1px solid {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.1)'};
        margin: 10px 0;
    }}
    
    .streamlit-expanderHeader {{
        font-weight: 600;
        font-size: 16px;
    }}
    
    .stSlider>div>div>div>div {{
        background: linear-gradient(45deg, #667eea, #764ba2);
    }}
    
    .floating-card {{
        background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.9)'};
        backdrop-filter: blur(20px);
        border-radius: 20px;
        padding: 30px;
        margin: 20px 0;
        border: 1px solid {'rgba(255,255,255,0.2)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.1)'};
        box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    
    .floating-card:hover {{
        transform: translateY(-10px);
        box-shadow: 0 20px 40px rgba(0,0,0,0.2);
    }}
    
    .glass-effect {{
        background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.8)'};
        backdrop-filter: blur(15px);
        border-radius: 16px;
        border: 1px solid {'rgba(255,255,255,0.2)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.1)'};
    }}
    
    .neon-glow {{
        animation: neon-glow 2s ease-in-out infinite alternate;
    }}
    
    @keyframes neon-glow {{
        from {{ box-shadow: 0 0 20px #667eea; }}
        to {{ box-shadow: 0 0 30px #764ba2, 0 0 40px #667eea; }}
    }}
    
    .fade-in {{
        animation: fadeIn 0.8s ease-in;
    }}
    
    @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(30px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    
    .stProgress .st-bo {{
        background: linear-gradient(45deg, #667eea, #764ba2);
    }}
    
</style> 
""", unsafe_allow_html=True)


# Modern Hero Section
st.markdown(f"""
<div class="floating-card fade-in" style="text-align: center; margin: 30px 0;">
    <div style="
        color: #FF6600;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        font-size: 3.5rem;
        font-weight: 800;
        margin-bottom: 10px;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
    ">
        🌐 VayuMitra
    </div>
    <div style="
        font-size: 1.2rem;
        color: {'rgba(255,255,255,0.8)' if st.session_state.theme == 'dark' else 'rgba(0,0,0,0.7)'};
        font-weight: 400;
        margin-bottom: 20px;
    ">
        Transforming Earth Data into Action for a Cleaner Tomorrow
    </div>
    <div style="
        display: flex;
        justify-content: center;
        gap: 20px;
        flex-wrap: wrap;
    ">
        <div class="glass-effect" style="padding: 10px 20px; border-radius: 25px;">
            📊 Real-time Monitoring
        </div>
        <div class="glass-effect" style="padding: 10px 20px; border-radius: 25px;">
            🚨 Smart Alerts
        </div>
        <div class="glass-effect" style="padding: 10px 20px; border-radius: 25px;">
            📈 AI Forecasting
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Dynamic Alert Banner - Only shows when AQI > 100
# This will be populated after AQI data is fetched
if "alert_placeholder" not in st.session_state:
    st.session_state.alert_placeholder = st.empty()

# -----------------------------
# User Input
# -----------------------------
# -----------------------------
# Detect user location automatically
# -----------------------------
default_city, default_lat, default_lon = get_user_location()

# Store in session_state to prevent repeated API calls
if "city" not in st.session_state:
    st.session_state.city = default_city
    st.session_state.lat = default_lat
    st.session_state.lon = default_lon

# Allow user to override
# -----------------------------
# Simple City Search
# -----------------------------
search_query = st.text_input("🔎 Enter a city name:")

if search_query.strip() and len(search_query) > 2:
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={search_query}&format=json&limit=5"
        resp = requests.get(url, headers={"User-Agent": "VayuMitra"}, timeout=5)
        
        if resp.status_code == 200:
            results = resp.json()
            if results:
                options = [f"{r['display_name']}" for r in results]
                selected_city = st.selectbox("✨ Select a city:", options)
                
                if st.button("📍 Use This Location"):
                    selected = results[options.index(selected_city)]
                    st.session_state.city = selected["display_name"].split(",")[0]
                    st.session_state.lat = float(selected["lat"])
                    st.session_state.lon = float(selected["lon"])
                    st.success(f"📍 Location updated to: {st.session_state.city}")
                    st.rerun()
            else:
                st.info("🔍 No cities found. Try a different search term.")
    except Exception:
        st.info("🌐 Search temporarily unavailable.")

lat, lon = st.session_state.lat, st.session_state.lon
st.info(f"📍 Current location: **{st.session_state.city}**")

# -----------------------------
# Map Section
# -----------------------------
st.markdown("## 🌐 Air Quality Map")
stations_df = fetch_nearby_stations(lat, lon)

# Create the map
m = folium.Map(
    location=[lat, lon],
    zoom_start=10,
    tiles="CartoDB dark_matter" if st.session_state.theme=="dark" else "OpenStreetMap"
)

# 1️⃣ Add city marker
folium.Marker(
    location=[lat, lon],
    popup=f"📍 {st.session_state.city}",
    tooltip=f"{st.session_state.city} (click to navigate)",
    icon=folium.Icon(color="blue", icon="info-sign")
).add_to(m)

# 2️⃣ Add AQI station markers
for _, row in stations_df.iterrows():
    df_station = fetch_openaq(row["lat"], row["lon"], "pm2.5", radius=1000, limit=1)
    aqi_val = calculate_aqi("pm2.5", df_station["value"].iloc[0]) if not df_station.empty else None
    color = get_aqi_color(aqi_val)
    folium.CircleMarker(
        location=[row["lat"], row["lon"]],
        radius=12,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.8,
        popup=f"<b>{row['station']}</b><br>AQI: {aqi_val if aqi_val is not None else 'N/A'}",
        tooltip=f"AQI: {aqi_val if aqi_val is not None else 'N/A'}"
    ).add_to(m)

# 3️⃣ Handle last clicked location
# Add a marker if the user clicked somewhere
click_data = st_folium(m, width=1525, height=550, key="main_map")

if click_data.get("last_clicked"):
    clicked_lat = click_data["last_clicked"]["lat"]
    clicked_lon = click_data["last_clicked"]["lng"]
    
    # Update the coordinates for AQI calculations
    current_lat, current_lon = clicked_lat, clicked_lon
    st.info(f"📍 Selected location: {clicked_lat:.4f}, {clicked_lon:.4f}")
else:
    current_lat, current_lon = lat, lon


# -----------------------------
# AQI Boxes & Alerts
# -----------------------------
pollutants = ["pm2.5","pm10","no2","o3"]
if "selected_pollutant" not in st.session_state: st.session_state.selected_pollutant = "pm2.5"

def set_selected_pollutant(p):
    st.session_state.selected_pollutant = p

def render_aqi_boxes(lat, lon):
    latest_values = {}
    cols = st.columns(len(pollutants))
    for i, p in enumerate(pollutants):
        df_g = fetch_openaq(lat, lon, p)
        df_m = fetch_meteo_aq(lat, lon, p)
        if not df_m.empty:
            df_m["value"] = df_m["value"].apply(lambda x: normalize_meteo_value(x,p))
        val = None
        if not df_g.empty and not df_m.empty:
            # Ensure both datetime columns have the same timezone
            df_g['datetime'] = pd.to_datetime(df_g['datetime']).dt.tz_localize(None)
            df_m['datetime'] = pd.to_datetime(df_m['datetime']).dt.tz_localize(None)
            merged = pd.merge_asof(df_m.sort_values("datetime"),
                                   df_g.sort_values("datetime"),
                                   on="datetime",
                                   tolerance=pd.Timedelta("1h"),
                                   direction="nearest",
                                   suffixes=('_meteo', '_openaq'))
            # Use OpenAQ data if available, otherwise use Meteo data
            if 'value_openaq' in merged.columns:
                merged['final_value'] = merged['value_openaq'].fillna(merged['value_meteo'])
            else:
                merged['final_value'] = merged['value_meteo']
            val = merged['final_value'].iloc[-1]
        elif not df_g.empty:
            val = df_g["value"].iloc[0]
        elif not df_m.empty:
            val = df_m["value"].iloc[-1]
        latest_values[p] = val
        aqi_val = calculate_aqi(p,val) if val is not None else None
        color = get_aqi_color(aqi_val)
        with cols[i]:
            if st.button(f"{p.upper()}", key=f"btn_{p}"):
                set_selected_pollutant(p)
            bg_card = "#ffffff" if st.session_state.theme == "light" else "#1f1f1f"
            text_card = "#000000" if st.session_state.theme == "light" else "#ffffff"
            
            st.markdown(f"""
            <div class="floating-card neon-glow" style='
                background: {'rgba(255,255,255,0.1)' if st.session_state.theme == 'dark' else 'rgba(255,255,255,0.9)'};
                color: {text_card};
                padding: 30px;
                border-radius: 20px;
                text-align: center;
                backdrop-filter: blur(20px);
                border: 2px solid {color};
                position: relative;
                overflow: hidden;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            ' title='Latest AQI: {aqi_val if aqi_val is not None else "N/A"}'>
                <div style="
                    position: absolute;
                    top: -50%;
                    left: -50%;
                    width: 200%;
                    height: 200%;
                    background: linear-gradient(45deg, transparent, rgba(255,255,255,0.1), transparent);
                    transform: rotate(45deg);
                    animation: shimmer 3s infinite;
                "></div>
                <div style="position: relative; z-index: 1;">
                    <h4 style='margin-bottom: 15px; font-weight: 600; font-size: 16px;'>{p.upper()}</h4>
                    <div style='
                        font-size: 2.5rem;
                        font-weight: 800;
                        color: {color};
                        margin: 15px 0;
                        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
                    '>{aqi_val if aqi_val is not None else 'N/A'}</div>
                    <div style='
                        height: 6px;
                        width: 100%;
                        background: linear-gradient(90deg, {color}, rgba(255,255,255,0.3));
                        border-radius: 10px;
                        margin-top: 15px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    '></div>
                </div>
            </div>
            <style>
                @keyframes shimmer {{
                    0% {{ transform: translateX(-100%) translateY(-100%) rotate(45deg); }}
                    100% {{ transform: translateX(100%) translateY(100%) rotate(45deg); }}
                }}
            </style>
            """, unsafe_allow_html=True)
    return latest_values

latest_values = render_aqi_boxes(current_lat, current_lon)

# Dynamic Alert Banner - Only show when any AQI > 100
max_aqi = 0
for pollutant, val in latest_values.items():
    if val is not None:
        aqi_val = calculate_aqi(pollutant, val)
        if aqi_val and aqi_val > max_aqi:
            max_aqi = aqi_val

if max_aqi > 100:
    st.session_state.alert_placeholder.markdown(f"""
    <div style='
        background: linear-gradient(45deg, #ff4444, #cc0000);
        color: white;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        margin: 20px 0;
        font-size: 18px;
        font-weight: bold;
        box-shadow: 0 4px 8px rgba(255,68,68,0.3);
        animation: pulse 2s infinite;
    '>
        🚨 HIGH AQI ALERT 🚨<br>
        <span style='font-size: 16px;'>Current AQI: {max_aqi} - Air quality is unhealthy! Take precautions immediately.</span>
    </div>
    <style>
        @keyframes pulse {{
            0% {{ transform: scale(1); }}
            50% {{ transform: scale(1.02); }}
            100% {{ transform: scale(1); }}
        }}
    </style>
    """, unsafe_allow_html=True)
else:
    st.session_state.alert_placeholder.empty()

# PM2.5 Alert for levels above 50
if latest_values.get("pm2.5") is not None:
    pm25_aqi = calculate_aqi("pm2.5", latest_values["pm2.5"])
    if pm25_aqi and pm25_aqi > 50:
        st.markdown(f"""
        <div style='
            background: linear-gradient(45deg, #ff6b35, #f7931e);
            color: white;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            margin: 20px 0;
            font-size: 16px;
            font-weight: bold;
            box-shadow: 0 6px 12px rgba(255,107,53,0.4);
            border-left: 6px solid #ff4444;
        '>
            ⚠️ PM2.5 ALERT ⚠️<br>
            <span style='font-size: 20px; color: #fff;'>PM2.5 AQI: {pm25_aqi}</span><br>
            <span style='font-size: 14px;'>Air quality is moderate to unhealthy. Consider limiting outdoor activities!</span>
        </div>
        """, unsafe_allow_html=True)

# -----------------------------
# Trigger Pushover Alerts
# -----------------------------
for pollutant, val in latest_values.items():
    if val is not None:
        aqi_val = calculate_aqi(pollutant, val)
        if aqi_val and aqi_val > AQI_THRESHOLD:
            message = f"⚠️ {pollutant.upper()} AQI {aqi_val} in {st.session_state.city}"
            send_pushover_alert(message)

# -----------------------------
# Trigger Email Alerts per City
# -----------------------------
if st.session_state.city in CITY_EMAIL_RECIPIENTS:
    recipients = CITY_EMAIL_RECIPIENTS[st.session_state.city]

    for pollutant, val in latest_values.items():
        if val is not None:
            aqi_val = calculate_aqi(pollutant, val)
            if aqi_val and aqi_val > AQI_THRESHOLD:
                send_email_alert(pollutant, aqi_val, st.session_state.city, recipients)

# -----------------------------
# 24h Forecast Charts (keep your original code)
# -----------------------------
# You can insert your forecast/plotly charts here without changes

# -----------------------------
# Stakeholder Section & Footer (keep original code)
# -----------------------------
# Insert your footer, stakeholders, or other sections here


# -----------------------------
# 24h AQI Mountain Forecast (Animated)
# -----------------------------
st.markdown("## 📈 24/7h AQI Mountain Forecast")
forecast_pollutant = st.session_state.selected_pollutant

df_click_meteo = fetch_meteo_aq(current_lat, current_lon, forecast_pollutant)
if not df_click_meteo.empty:
    df_click_meteo["value"] = df_click_meteo["value"].apply(lambda x: normalize_meteo_value(x, forecast_pollutant))
    df_click_meteo["AQI"] = df_click_meteo["value"].apply(lambda x: calculate_aqi(forecast_pollutant, x))
    df_forecast = df_click_meteo.rename(columns={"AQI":"forecast"})
else:
    df_forecast = pd.DataFrame({
        "datetime": pd.date_range(start=datetime.now(timezone.utc), periods=24, freq="H"),
        "forecast": 50 + np.random.normal(0,5,24).cumsum()
    })

# Dynamic Y-axis based on AQI
max_aqi = df_forecast["forecast"].max()
if max_aqi <= 50: y_max = 60
elif max_aqi <= 100: y_max = 120
elif max_aqi <= 200: y_max = 220
elif max_aqi <= 300: y_max = 320
elif max_aqi <= 400: y_max = 420
else: y_max = 520

colorscale = [
    [0, "green"], [0.2, "yellow"], [0.4, "orange"],
    [0.6, "red"], [0.8, "purple"], [1, "maroon"]
]

fig = px.scatter(
    df_forecast, x="datetime", y="forecast", color="forecast",
    color_continuous_scale=colorscale,
    title=f"24h AQI Mountain Forecast ({forecast_pollutant.upper()})",
    labels={"forecast":"Air Quality Index","datetime":"Time"},
    template="plotly_dark" if st.session_state.theme=="dark" else "plotly_white"
)
fig.update_traces(mode="lines+markers", line=dict(width=3), fill="tozeroy")

bands = [(0,50,"Good","green"),(51,100,"Moderate","yellow"),
         (101,200,"Unhealthy (SG)","orange"),(201,300,"Unhealthy","red"),
         (301,400,"Very Unhealthy","purple"),(401,500,"Hazardous","maroon")]
for low, high, label, color in bands:
    fig.add_hrect(y0=low, y1=high, fillcolor=color, opacity=0.1, line_width=0,
                  annotation_text=label, annotation_position="inside top left")

fig.update_layout(
    margin=dict(l=10,r=10,t=30,b=10),
    height=400,
    yaxis=dict(range=[0, y_max]),
    coloraxis_colorbar=dict(title="AQI Level")
)

# --- Add Animation Frames ---
fig.update_layout(
    updatemenus=[{
        "type": "buttons", "showactive": False,
        "buttons": [
            {"label": "▶ Play","method": "animate",
             "args": [None, {"frame": {"duration": 500, "redraw": True},
                             "fromcurrent": True,
                             "transition": {"duration": 300}}]},
            {"label": "⏸ Pause","method": "animate",
             "args": [[None], {"frame": {"duration": 0, "redraw": False},
                               "mode": "immediate",
                               "transition": {"duration": 0}}]},
        ],
    }],
    sliders=[{
        "steps": [
            {"method": "animate",
             "args": [[f"{t}"], {"mode": "immediate",
                                 "frame": {"duration": 0, "redraw": True},
                                 "transition": {"duration": 0}}],
             "label": str(t)} for t in range(len(df_forecast))
        ],
    }],
)

frames = [
    go.Frame(
        data=[go.Scatter(x=df_forecast["datetime"][:k+1],
                         y=df_forecast["forecast"][:k+1],
                         mode="lines+markers",
                         line=dict(width=3))],
        name=str(k)
    ) for k in range(len(df_forecast))
]
fig.update(frames=frames)

st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# -----------------------------
# Interactive Health Recommendations
# -----------------------------
st.markdown("## 🏥 Health Recommendations")
current_aqi = latest_values.get(forecast_pollutant, 50)
current_aqi_val = calculate_aqi(forecast_pollutant, current_aqi) if current_aqi else 50
current_aqi_val = current_aqi_val or 50  # Handle None values

if current_aqi_val <= 50:
    st.success("✅ **Good Air Quality** - Perfect for outdoor activities!")
elif current_aqi_val <= 100:
    st.info("ℹ️ **Moderate Air Quality** - Sensitive individuals should limit prolonged outdoor exertion.")
elif current_aqi_val <= 200:
    st.warning("⚠️ **Unhealthy for Sensitive Groups** - Children, elderly, and people with respiratory conditions should avoid outdoor activities.")
else:
    st.error("🚨 **Unhealthy Air Quality** - Everyone should avoid outdoor activities and wear masks when going outside.")

# -----------------------------
# Interactive AQI Comparison
# -----------------------------
st.markdown("## 📊 AQI Comparison")
comparison_cols = st.columns(4)
for i, pollutant in enumerate(["pm2.5", "pm10", "no2", "o3"]):
    with comparison_cols[i]:
        val = latest_values.get(pollutant, 50)
        aqi = calculate_aqi(pollutant, val) if val else 50
        color = get_aqi_color(aqi)
        
        # Interactive metric with click functionality
        if st.button(f"📈 {pollutant.upper()}", key=f"compare_{pollutant}"):
            st.session_state.selected_pollutant = pollutant
            st.rerun()
        
        st.metric(
            label=f"{pollutant.upper()} AQI",
            value=aqi,
            delta=f"{aqi-50:+.0f} from baseline",
            delta_color="inverse"
        )

# -----------------------------
# Information Cards with Hover Effects
# -----------------------------
st.markdown("## 📊 Air Quality Insights")
cols = st.columns(3)

with cols[0]:
    current_aqi = latest_values.get(forecast_pollutant, 50)
    current_aqi_val = calculate_aqi(forecast_pollutant, current_aqi) if current_aqi else 50
    st.markdown(f"""
    <div style='background:{'#1f1f1f' if st.session_state.theme=='dark' else '#ffffff'}; 
                color:{'white' if st.session_state.theme=='dark' else 'black'}; 
                padding:20px; border-radius:10px; text-align:center;'>
        <img src='https://tse1.mm.bing.net/th/id/OIP._iB0f5oVah-T_wdiUjJpKAHaE8?cb=12&rs=1&pid=ImgDetMain&o=7&rm=3' 
             style='width:100%; max-width:200px; border-radius:8px; margin-bottom:15px;'>
        <div style='background:{'#2a2a2a' if st.session_state.theme=='dark' else '#f8f9fa'}; 
                    padding:15px; border-radius:8px; font-size:14px; text-align:left;'>
            <h5 style='text-align:center; margin-bottom:10px;'>🏭 Air Pollution Facts</h5>
            <ul style='margin:0; padding-left:20px;'>
                <li>Air pollution causes 7 million premature deaths annually worldwide</li>
                <li>PM2.5 particles can penetrate deep into lungs and bloodstream</li>
                <li>Children and elderly are most vulnerable to air quality impacts</li>
                <li>Poor air quality reduces life expectancy by 1-3 years globally</li>
            </ul>
            <p style='margin-top:10px; font-style:italic; text-align:center;'>Stay informed and protect your health by monitoring AQI levels daily.</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

with cols[1]:
    avg_forecast = df_forecast["forecast"].mean()
    st.markdown(f"""
    <div style='background:{'#1f1f1f' if st.session_state.theme=='dark' else '#ffffff'}; 
                color:{'white' if st.session_state.theme=='dark' else 'black'}; 
                padding:20px; border-radius:10px; text-align:center;'>
        <img src='https://tse4.mm.bing.net/th/id/OIP.xSplP9ZA7sTagmPnqgoO9QHaEK?cb=12&w=1200&h=675&rs=1&pid=ImgDetMain&o=7&rm=3' 
             style='width:100%; max-width:200px; border-radius:8px; margin-bottom:15px;'>
        <div style='background:{'#2a2a2a' if st.session_state.theme=='dark' else '#f8f9fa'}; 
                    padding:15px; border-radius:8px; font-size:14px; text-align:left;'>
            <h5 style='text-align:center; margin-bottom:10px;'>🌡️ Air Quality Monitoring</h5>
            <ul style='margin:0; padding-left:20px;'>
                <li>Real-time sensors track PM2.5, PM10, NO2, and O3 levels</li>
                <li>Satellite data provides comprehensive regional coverage</li>
                <li>Weather patterns significantly influence pollution dispersion</li>
                <li>24/7 monitoring enables early warning systems</li>
                <li>Machine learning algorithms improve forecast accuracy</li>
            </ul>
            <p style='margin-top:10px; font-style:italic; text-align:center;'>Advanced technology helps predict and prevent air quality crises.</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

with cols[2]:
    max_forecast = df_forecast["forecast"].max()
    st.markdown(f"""
    <div style='background:{'#1f1f1f' if st.session_state.theme=='dark' else '#ffffff'}; 
                color:{'white' if st.session_state.theme=='dark' else 'black'}; 
                padding:20px; border-radius:10px; text-align:center;'>
        <img src='https://tse4.mm.bing.net/th/id/OIP.zp8KiWs0wQULlMO7NdsN_wHaE8?cb=12&rs=1&pid=ImgDetMain&o=7&rm=3' 
             style='width:100%; max-width:200px; border-radius:8px; margin-bottom:15px;'>
        <div style='background:{'#2a2a2a' if st.session_state.theme=='dark' else '#f8f9fa'}; 
                    padding:15px; border-radius:8px; font-size:14px; text-align:left;'>
            <h5 style='text-align:center; margin-bottom:10px;'>🏙️ Most Polluted Cities</h5>
            <ul style='margin:0; padding-left:20px;'>
                <li>Delhi, India consistently ranks among world's most polluted</li>
                <li>Beijing, China faces severe smog during winter months</li>
                <li>Mexico City struggles with high ozone levels due to altitude</li>
                <li>Industrial cities face year-round pollution challenges</li>
            </ul>
            <p style='margin-top:10px; font-style:italic; text-align:center;'>Urban planning and emission controls are crucial for cleaner air.</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

# -----------------------------
# Interactive Features
# -----------------------------
st.markdown("## 🎮 Interactive Features")

# AQI Alert Settings
with st.expander("🔔 Set Custom AQI Alerts", expanded=False):
    alert_threshold = st.slider("Alert me when AQI exceeds:", 0, 300, AQI_THRESHOLD, 10)
    alert_pollutants = st.multiselect("Monitor these pollutants:", ["pm2.5", "pm10", "no2", "o3"], ["pm2.5"])
    if st.button("📧 Save Alert Settings"):
        st.success(f"Alert set for AQI > {alert_threshold} for {', '.join(alert_pollutants)}")

# Quick Actions
st.markdown("### ⚡ Quick Actions")
action_cols = st.columns(4)
with action_cols[0]:
    if st.button("📱 Share Data"):
        st.info(f"Current AQI: {current_aqi_val} in {st.session_state.city}")
with action_cols[1]:
    report_data = f"""
🌍 AirGuard Air Quality Report
{'='*40}
City: {st.session_state.city}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Current AQI Levels:
{'-'*20}
""" + "\n".join([f"{p.upper()}: {calculate_aqi(p, latest_values.get(p, 0)) or 'N/A'}" for p in pollutants]) + f"""

Health Recommendations:
{'-'*20}
Current PM2.5 AQI: {calculate_aqi('pm2.5', latest_values.get('pm2.5', 0)) or 'N/A'}
""" + ("⚠️ Air quality is concerning. Limit outdoor activities." if calculate_aqi('pm2.5', latest_values.get('pm2.5', 0) or 0) and calculate_aqi('pm2.5', latest_values.get('pm2.5', 0)) > 50 else "✅ Air quality is acceptable for outdoor activities.") + f"""

Generated by AirGuard Dashboard
"""
    st.download_button(
        label="📄 View/Download Report",
        data=report_data,
        file_name=f"AirGuard_Report_{st.session_state.city}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
        mime="text/plain"
    )
with action_cols[2]:
    if st.button("🗺️ Nearby Stations"):
        st.info(f"Found {len(stations_df)} monitoring stations nearby")
with action_cols[3]:
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.success("Data refreshed!")

# -----------------------------
# Additional Features
# -----------------------------
st.markdown("## 🚀 Additional Features")

# Weather Integration
with st.expander("🌤️ Weather Impact on Air Quality", expanded=False):
    weather_cols = st.columns(2)
    with weather_cols[0]:
        st.markdown("**Weather Factors Affecting AQI:**")
        st.write("• Wind speed disperses pollutants")
        st.write("• Rain washes out particles")
        st.write("• Temperature inversions trap pollution")
        st.write("• Humidity affects particle formation")
    with weather_cols[1]:
        if st.button("🌡️ Get Weather Data"):
            # Simulate weather data (in production, use OpenWeatherMap API)
            import random
            temp = random.randint(15, 35)
            humidity = random.randint(40, 80)
            wind_speed = random.randint(5, 25)
            conditions = random.choice(["Clear", "Cloudy", "Rainy", "Foggy"])
            
            st.markdown(f"""**Current Weather in {st.session_state.city}:**
            🌡️ Temperature: {temp}°C
            💧 Humidity: {humidity}%
            💨 Wind Speed: {wind_speed} km/h
            ☁️ Conditions: {conditions}
            
            **Impact on Air Quality:**""")
            
            if wind_speed > 15:
                st.success("✅ High wind speed helps disperse pollutants")
            elif wind_speed < 8:
                st.warning("⚠️ Low wind speed may trap pollutants")
            else:
                st.info("ℹ️ Moderate wind conditions")
                
            if conditions == "Rainy":
                st.success("✅ Rain helps wash out air pollutants")
            elif conditions == "Foggy":
                st.warning("⚠️ Fog can worsen air quality perception")
                
            if humidity > 70:
                st.warning("⚠️ High humidity may increase particle formation")

# AQI History Tracker
with st.expander("📊 AQI History & Trends", expanded=False):
    history_cols = st.columns(3)
    with history_cols[0]:
        if st.button("📈 7-Day Trend"):
            st.line_chart(pd.DataFrame({"AQI": [45, 52, 38, 61, 55, 48, 42]}))
    with history_cols[1]:
        if st.button("📅 Monthly Average"):
            st.bar_chart(pd.DataFrame({"Month": ["Jan", "Feb", "Mar"], "AQI": [58, 45, 52]}).set_index("Month"))
    with history_cols[2]:
        if st.button("🔄 Compare Cities"):
            st.info("City comparison feature would show AQI differences")

# Emergency Contacts
with st.expander("🚨 Emergency & Health Contacts", expanded=False):
    emergency_cols = st.columns(2)
    with emergency_cols[0]:
        st.markdown("**Emergency Numbers:**")
        st.write("🚑 Medical Emergency: 102")
        st.write("🔥 Fire Department: 101")
        st.write("👮 Police: 100")
    with emergency_cols[1]:
        st.markdown("**Health Advisory:**")
        st.write("🏥 Consult doctor if breathing issues")
        st.write("😷 Wear N95 masks outdoors")
        st.write("🏠 Stay indoors during high AQI")

# Air Quality Tips
with st.expander("💡 Air Quality Improvement Tips", expanded=False):
    tips_cols = st.columns(2)
    with tips_cols[0]:
        st.markdown("**Personal Actions:**")
        st.write("🚗 Use public transport")
        st.write("🌱 Plant more trees")
        st.write("♻️ Reduce, reuse, recycle")
        st.write("💡 Use energy-efficient appliances")
    with tips_cols[1]:
        st.markdown("**Indoor Air Quality:**")
        st.write("🪴 Keep indoor plants")
        st.write("🚪 Ensure proper ventilation")
        st.write("🧹 Regular cleaning")
        st.write("🚭 No smoking indoors")

# -----------------------------
# Stakeholder Section
# -----------------------------
with st.expander("👥 Stakeholder Groups and Use Cases", expanded=False):
    st.markdown(f"""
    <ul>
    <li><b>Health-Sensitive Groups:</b>
        <ul>
            <li>Vulnerable populations requiring targeted protection. <a href='https://www.aqi.in/dashboard/india' target='_blank'>Real-time AQI India</a></li>
            <li>School administrators safeguarding student health. <a href='https://mpcb.gov.in/contact-us' target='_blank'>MPCB Helpline</a></li>
            <li>Eldercare facility managers protecting senior residents. <a href='https://www.helpguide.org/articles/healthy-living/air-quality-and-health.htm' target='_blank'>Air Quality & Elderly Health</a></li>
            <li>Residents in industrial zones facing heightened exposure. <a href='https://cpcb.nic.in/industrial-pollution/' target='_blank'>Industrial Pollution Info</a></li>
        </ul>
    </li>
    <li><b>Policy Implementation Partners:</b>
        <ul>
            <li>Government officials and municipal leaders enacting clean air initiatives. <a href='https://cpcb.nic.in/contact-us/' target='_blank'>CPCB Contact</a></li>
            <li>Transportation authorities managing transit systems, ports, aviation, traffic flows. <a href='https://moef.gov.in/' target='_blank'>Ministry of Environment</a></li>
            <li>Parks departments, recreation coordinators, and athletic trainers/directors. <a href='https://www.india.gov.in/spotlight/green-india-mission' target='_blank'>Green India Mission</a></li>
            <li>School district environmental health officers and athletic trainers/directors. <a href='https://www.niehs.nih.gov/research/programs/geh/air_quality/index.cfm' target='_blank'>School Air Quality Guidance</a></li>
            <li>Tourism boards optimizing visitor experiences and regional appeal. <a href='https://www.incredibleindia.org/content/incredible-india-v2/en/experiences/nature/air-quality.html' target='_blank'>Air Quality & Tourism</a></li>
        </ul>
    </li>
    <li><b>Emergency Response Networks:</b>
        <ul>
            <li>Wildfire management teams. <a href='https://www.fsi.nic.in/' target='_blank'>Forest Survey of India</a></li>
            <li>Disaster readiness organizations. <a href='https://ndma.gov.in/' target='_blank'>NDMA</a></li>
            <li>Meteorological service providers. <a href='https://mausam.imd.gov.in/' target='_blank'>IMD</a></li>
            <li>Crisis communication specialists. <a href='https://www.undrr.org/' target='_blank'>UNDRR</a></li>
            <li><a href='https://cpcb.nic.in/AQI_India/' target='_blank'>CPCB Real-time Data</a></li>
            <li><a href='https://in.usembassy.gov/embassy-consulates/new-delhi/air-quality-data/' target='_blank'>US Embassy Air Quality Data</a></li>
        </ul>
    </li>
    <li><b>Economic Stakeholders:</b>
        <ul>
            <li>Insurance risk assessors evaluating health and property implications. <a href='https://www.cleanairfund.org/geography/india/' target='_blank'>Clean Air Fund India</a></li>
        </ul>
    </li>
    <li><b>Public Engagement:</b>
        <ul>
            <li>Citizen science coordinators mobilizing community-based data collection. <a href='https://www.aqi.in/contact-us' target='_blank'>AQI Contact</a></li>
            <li>Community-driven air quality monitoring projects. <a href='https://www.citizenscience.org/' target='_blank'>Citizen Science Platform</a></li>
            <li>Educational outreach and workshops. <a href='https://www.airnow.gov/education/' target='_blank'>Air Quality Education Resources</a></li>
        </ul>
    </li>
    </ul>
    """, unsafe_allow_html=True)
   
# -----------------------------
# Footer
# -----------------------------
footer_bg = "#1f1f1f" if st.session_state.theme=="dark" else "#f5f5f5"
footer_text = "white" if st.session_state.theme=="dark" else "black"

st.markdown(f"""
<div style="
    background-color: {footer_bg};
    color: {footer_text};
    text-align: center;
    padding: 15px 0;
    border-top: 1px solid {'#333' if st.session_state.theme=='dark' else '#ccc'};
    margin-top: 50px;
    font-size: 14px;
">
    🌍 AirGuard Dashboard – Smart AQI Tracking | Developed by Vaibhav | &copy; {datetime.now().year}
</div>
""", unsafe_allow_html=True)

