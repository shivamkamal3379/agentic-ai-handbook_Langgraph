import os
import pandas as pd
import numpy as np
import struct
import socket
import subprocess
import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output, State
import xgboost as xgb
import plotly.express as px
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, precision_score, recall_score, accuracy_score, f1_score
import requests
import geoip2.database
import joblib
from datetime import datetime

# ==== Paths ====
LOG_FILE = "/var/log/snort/finalalert.csv"
XGB_MODEL_PATH = "xgboost_model_binary.json"
ISO_MODEL_PATH = "isolation_forest_model.joblib"
GEO_DB_PATH = "GeoLite2-City.mmdb"

# ==== Load Models ====
xgb_model = xgb.Booster()
xgb_model.load_model(XGB_MODEL_PATH)
iso_model = joblib.load(ISO_MODEL_PATH)
geo_reader = geoip2.database.Reader(GEO_DB_PATH)

# ==== AbuseIPDB API Key ====
ABUSEIPDB_API_KEY = "0489f417f1c4badb64a33968ec720c21c6a19190d4d6e5175ac94e2a0fc6e622ed9a15c382049092"

# ==== Mappings ====
CLASS_TO_LABEL = {0: "Normal", 1: "Attack"}
ANOMALY_LABEL = {-1: "Anomaly", 1: "Normal"}

ip_cache = {}
blocked_ips = set()
max_accuracy = 0.0

def ip_to_int(ip):
    try:
        return struct.unpack("!I", socket.inet_aton(ip))[0]
    except:
        return np.nan

def get_ip_reputation_cached(ip):
    if ip in ip_cache:
        return ip_cache[ip]
    url = f"https://api.abuseipdb.com/api/v2/check"
    headers = {'Key': ABUSEIPDB_API_KEY, 'Accept': 'application/json'}
    params = {'ipAddress': ip, 'maxAgeInDays': '90'}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        data = response.json()
        score = data.get("data", {}).get("abuseConfidenceScore", None)
        ip_cache[ip] = score
        return score
    except Exception as e:
        print(f"Error getting reputation for {ip}: {e}")
        return None

def get_country_from_ip(ip):
    try:
        response = geo_reader.city(ip)
        return response.country.name
    except:
        return "Unknown"

def block_ip(ip):
    if ip in blocked_ips:
        return False
    try:
        socket.inet_aton(ip)
        subprocess.run(["sudo", "iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"], check=True)
        blocked_ips.add(ip)
        print(f"Blocked IP: {ip}")
        return True
    except Exception as e:
        print(f"Failed to block IP {ip}: {e}")
        return False

def unblock_ip(ip):
    if ip not in blocked_ips:
        return False
    try:
        socket.inet_aton(ip)
        subprocess.run(["sudo", "iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"], check=True)
        blocked_ips.remove(ip)
        print(f"Unblocked IP: {ip}")
        return True
    except Exception as e:
        print(f"Failed to unblock IP {ip}: {e}")
        return False

def preprocess_data(filepath):
    try:
        df = pd.read_csv(filepath, header=None)
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()

        df.columns = ['Timestamp', 'Event Id', 'Sid', 'Severity', 'Signature title', 'Protocol',
                      'Src IP', 'Src Port', 'Dst IP', 'Dst Port', 'Source MAC', 'Destination MAC',
                      'Ethernet Type', 'Flags', 'Sequence Number', 'Ack Number', 'Unnamed: 16',
                      'Window Size', 'TTL', 'IP header Length', 'Total Length', 'Total Packet',
                      'Window size in Bytes', 'ICMP Type', 'ICMP Code', 'Sq Number', 'Identifier']

        df = df.tail(100)

        df.loc[:, 'Src IP Encoded'] = df['Src IP'].apply(ip_to_int)
        df.loc[:, 'Dst IP Encoded'] = df['Dst IP'].apply(ip_to_int)
        df.loc[:, "Protocol"] = df["Protocol"].map({'ICMP': 0, 'TCP': 1, 'UDP': 2})
        df["Protocol"] = pd.to_numeric(df["Protocol"], errors="coerce")

        features = ["Event Id", "Protocol", "Src Port", "Dst Port", "TTL",
                    "IP header Length", "Total Length", "Total Packet",
                    "Window size in Bytes", "Src IP Encoded", "Dst IP Encoded"]

        df_features = df[features].dropna().astype(float)
        df_display = df[['Timestamp', 'Sid', 'Signature title', 'Src Port', 'Src IP', 'Dst Port', 'Dst IP']].iloc[-len(df_features):].reset_index(drop=True)

        df_display = df_display.copy()
        df_display['Src IP Reputation'] = None
        df_display['Src Country'] = None

        for ip in df_display['Src IP'].unique()[:5]:
            score = get_ip_reputation_cached(ip)
            country = get_country_from_ip(ip)
            df_display.loc[df_display['Src IP'] == ip, 'Src IP Reputation'] = score
            df_display.loc[df_display['Src IP'] == ip, 'Src Country'] = country

        return df_features, df_display
    except Exception as e:
        print("Error in preprocessing:", e)
        return pd.DataFrame(), pd.DataFrame()

# ==== Dash App ====
app = dash.Dash(__name__)
app.title = "Snort Live Dashboard"

def generate_blocked_ips_table_data():
    return [{"Blocked IP": ip, "Unblock": f"Unblock {ip}"} for ip in blocked_ips]

app.layout = html.Div([
    html.H1("Snort Live Dashboard (WOA-XGBoost and Isolation Forest)", style={'textAlign': 'center', 'color': 'darkblue'}),

    html.Div([
        html.H4(id='accuracy-output', style={'textAlign': 'center', 'color': 'green'}),
        html.H5(id='max-accuracy-output', style={'textAlign': 'center', 'color': 'orange'})
    ]),

    dcc.Interval(id='interval-component', interval=5000, n_intervals=0),

    dash_table.DataTable(
        id='live-table',
        columns=[{"name": col, "id": col} for col in ["Timestamp", "Sid", "Signature title", "Src Port", "Src IP", "Dst Port", "Dst IP",
                                                      "XGBoost Prediction", "Isolation Forest", "Src IP Reputation", "Src Country"]],
        style_table={'overflowX': 'auto', 'height': '40vh', 'overflowY': 'scroll'},
        style_header={'backgroundColor': 'lightblue', 'fontWeight': 'bold'},
        style_cell={'textAlign': 'center'}
    ),

    html.Br(),

    html.H2("Blocked IPs", style={'textAlign': 'center', 'color': 'red'}),
    dash_table.DataTable(
        id='blocked-ips-table',
        columns=[
            {"name": "Blocked IP", "id": "Blocked IP"},
            {"name": "UnBlock", "id": "Unblock"}
        ],
        data=generate_blocked_ips_table_data(),
        style_table={'width': '50%', 'margin': 'auto', 'overflowX': 'auto', 'maxHeight': '20vh', 'overflowY': 'scroll'},
        style_header={'backgroundColor': 'pink', 'fontWeight': 'bold'},
        style_cell={'textAlign': 'center'},
    ),

    html.Br(),

    html.Div([
        dcc.Graph(id='attack-chart'),
        dcc.Graph(id='confusion-matrix'),
        dcc.Graph(id='geo-map'),
        dcc.Graph(id='accuracy-comparison')
    ]),

    html.Div([
        html.H3("Performance Metrics"),
        html.Div(id='metrics-output', style={'textAlign': 'center', 'fontSize': '18px'})
    ])
])

@app.callback(
    [Output('live-table', 'data'),
     Output('confusion-matrix', 'figure'),
     Output('metrics-output', 'children'),
     Output('accuracy-output', 'children'),
     Output('max-accuracy-output', 'children'),
     Output('attack-chart', 'figure'),
     Output('geo-map', 'figure'),
     Output('accuracy-comparison', 'figure')],
     #Output('blocked-ips-table', 'data')],
    Input('interval-component', 'n_intervals')
)
def update_dashboard(n):
    global max_accuracy, blocked_ips

    df_features, df_display = preprocess_data(LOG_FILE)
    if df_features.empty or df_display.empty:
        empty_fig = px.imshow([[0]])
        empty_fig.update_layout(title="No Data")
        return [], empty_fig, "No data available", "", "", empty_fig, empty_fig, empty_fig, generate_blocked_ips_table_data()

    dmatrix = xgb.DMatrix(df_features)
    xgb_preds_proba = xgb_model.predict(dmatrix)
    xgb_preds = (xgb_preds_proba > 0.5).astype(int)
    iso_preds = iso_model.predict(df_features)
    iso_preds_label = [ANOMALY_LABEL[p] for p in iso_preds]

    df_display["XGBoost Prediction"] = [CLASS_TO_LABEL[p] for p in xgb_preds]
    df_display["Isolation Forest"] = iso_preds_label

    y_true = (df_display['Sid'] != 10000003).astype(int)
    accuracy = accuracy_score(y_true, xgb_preds)
    precision = precision_score(y_true, xgb_preds, zero_division=0)
    recall = recall_score(y_true, xgb_preds, zero_division=0)
    f1 = f1_score(y_true, xgb_preds, zero_division=0)
    cm = confusion_matrix(y_true, xgb_preds, labels=[0, 1])

    if accuracy > max_accuracy:
        max_accuracy = accuracy

    attack_counts = df_display['XGBoost Prediction'].value_counts().reset_index()
    attack_counts.columns = ['Type', 'Count']
    bar_fig = px.bar(attack_counts, x='Type', y='Count', color='Type', title='Attack vs Normal Counts')

    cm_fig = px.imshow(cm, text_auto=True, labels=dict(x="Predicted", y="Actual"), x=["Normal", "Attack"], y=["Normal", "Attack"])
    cm_fig.update_layout(title="Confusion Matrix")

    df_attack = df_display[df_display["XGBoost Prediction"] == "Attack"]
    if not df_attack.empty:
        df_attack = df_attack.copy()
        df_attack['Country'] = df_attack['Src IP'].apply(get_country_from_ip)
        country_counts = df_attack['Country'].value_counts().reset_index()
        country_counts.columns = ['Country', 'Count']
        geo_fig = px.choropleth(country_counts, locations="Country", locationmode='country names',
                                color="Count", hover_name="Country",
                                title="Attack Sources by Country", color_continuous_scale="Reds")
    else:
        geo_fig = px.scatter(title="No attack IPs detected")

    accuracy_fig = px.bar(x=["Current Accuracy", "Max Accuracy"], y=[accuracy, max_accuracy],
                          labels={"x": "Metric", "y": "Accuracy"}, title="Current vs Max Accuracy")

    for ip, pred, proba in zip(df_display['Src IP'], xgb_preds, xgb_preds_proba):
        if pred == 1 and proba > 0.8 and ip not in blocked_ips:
            block_ip(ip)

    metrics_text = f"Accuracy: {accuracy:.3f} | Precision: {precision:.3f} | Recall: {recall:.3f} | F1 Score: {f1:.3f}"
    #return df_display.to_dict('records'), cm_fig, metrics_text, f"Accuracy: {accuracy:.3f}", f"Max Accuracy: {max_accuracy:.3f}", bar_fig, geo_fig, accuracy_fig, generate_blocked_ips_table_data()
    return (
        df_display.to_dict('records'),
        cm_fig,
        metrics_text,
        f"Accuracy: {accuracy:.3f}",
        f"Max Accuracy: {max_accuracy:.3f}",
        bar_fig,
        geo_fig,
        accuracy_fig
    )

@app.callback(
    Output('blocked-ips-table', 'data'),
    Input('blocked-ips-table', 'active_cell'),
    State('blocked-ips-table', 'data')
)
def unblock_ip_callback(active_cell, table_data):
    if active_cell and active_cell['column_id'] == 'Unblock':
        ip_to_unblock = table_data[active_cell['row']]['Blocked IP']
        unblock_ip(ip_to_unblock)
    return generate_blocked_ips_table_data()


# ==== Run App ====
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)

