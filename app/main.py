from flask import Flask, request, render_template, jsonify, redirect, session
from flask_session import Session
import spotipy
import spotipy.util as util
from spotipy.oauth2 import SpotifyOAuth
import json
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from pandas_gbq import to_gbq
import pickle

## Credentials
# Spotify API credentials
scope = 'user-read-private user-top-read playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private'
client_id = '4ae185ed0ffc45739137a872c94f1623' 
client_secret = '54f73d9abd314200a24d52bd54f52a05'  
redirect_uri = 'http://35.224.240.230:8080/callback/' 

# BigQuery credentials
key_path = 'keys/hybrid-entropy-399823-f4a5a77b4128.json'  
client = bigquery.Client(credentials=service_account.Credentials.from_service_account_file(key_path))

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
app.config['SECRET_KEY'] = 'sdfjkeji1421359186'
Session(app)

@app.route('/', methods=['GET', 'POST'])
def login():
    # username = request.form['username']
    # session['username'] = username
    sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=scope, open_browser=False)
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback/', methods=['GET'])
def callback():
    code = request.args.get('code')
    if code:
        # username = session.get('username')
        sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=scope, open_browser=False)
        sp = spotipy.Spotify(auth_manager=sp_oauth)
        # user_results = sp.current_user()
        # username = results['id']
        username = '1216926599'
        token = sp_oauth.get_access_token(code)
        retrieve_spotify_data(username, token)
        ## Recommend songs
        recommended_tracks = recommend(username)
        ## Push playlist into Spotify
        create_and_replace_playlist(recommended_tracks, token)
        return jsonify({"message": "Listen with Nakul playlist created! Happy listening!"})
    else:
        return jsonify({"error": "Authentication Failed"})
    
def retrieve_spotify_data(username, token):
    sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=scope, open_browser=False)
    sp = spotipy.Spotify(auth_manager=sp_oauth)
    results = sp.current_user_top_tracks(limit=50, offset=0, time_range='medium_term')
    track_data = []

    for item in results['items']:
        track_id = item['id']
        artists = [artist['name'] for artist in item['artists']]
        track_name = item['name']
        track_data.append([track_id, artists, track_name])

    df = pd.DataFrame(track_data, columns=['track_id', 'artists', 'track_name'])
    track_ids = df['track_id'].tolist()
    track_ids = [str(item) for item in track_ids]

    batch_size = 15
    track_features = []
    n = 0

    for i in range(0, len(track_ids), batch_size):
        batch_track_ids = track_ids[i:i + batch_size]
        n += 1

        for _ in range(3):  # Retry for a limited number of times
            try:
                features = sp.audio_features(batch_track_ids)
                if features:
                    for feature in features:
                        if feature is not None:
                            track_features.append([feature['danceability'], feature['energy'], feature['loudness'], feature['speechiness'], feature['acousticness'], feature['instrumentalness'], feature['liveness'], feature['valence'], feature['tempo']])
                        else:
                            track_features.append([None, None, None, None, None, None, None, None, None])
                    break
            except spotipy.SpotifyException as e:
                if e.http_status == 429:
                    print("Rate limited. Waiting and retrying...")
                else:
                    print("Error:", e)

    feature_columns = ['danceability', 'energy', 'loudness', 'speechiness', 'acousticness', 'instrumentalness', 'liveness', 'valence', 'tempo']
    song_features = pd.DataFrame(track_features, columns=feature_columns)
    song_features['track_id'] = track_ids

    DATASET_ID = 'spotify_data'

    table_name = f'{username}_top_tracks'

    dataset_ref = client.dataset(DATASET_ID)
    tables = client.list_tables(dataset_ref)
    table_exists = any(table.table_id == table_name for table in tables)

    if not table_exists:
        to_gbq(song_features, f'{DATASET_ID}.{table_name}', if_exists='replace')
        
def recommend(username):
    DATASET_ID = 'spotify_data'
    table_name = f'{username}_top_tracks'
    
    # Use BigQuery ML to generate recommendations (replace with your own SQL statement)
    sql_query = f"""
        SELECT
            track_id,
            predicted_like_dislike_probs[OFFSET(0)].prob as probability_like
        FROM
            ML.PREDICT(MODEL `{DATASET_ID}.log_model`,
            (
            SELECT
                *
            FROM {DATASET_ID}.{table_name}))
        ORDER BY 2 DESC
        LIMIT 20;
    """
    
    query_job = client.query(sql_query)
    results = query_job.result()
    
    recommended_tracks = [row.track_id for row in results]
    return recommended_tracks
    
def create_and_replace_playlist(recommended_tracks, token):
    #authenticate
    sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=scope, open_browser=False)
    sp = spotipy.Spotify(auth_manager=sp_oauth)
    # Check if the playlist already exists
    existing_playlists = sp.current_user_playlists()
    for playlist in existing_playlists['items']:
        if playlist['name'] == 'Listen with Nakul':
            # Delete the existing playlist
            sp.current_user_unfollow_playlist(playlist['id'])
            break
    
    # Create a new playlist
    playlist_name = 'Listen with Nakul'
    user_data = sp.me()
    user = user_data['id']
    playlist = sp.user_playlist_create(user=user, name=playlist_name, public=False)
    
    # Add recommended tracks to the new playlist
    track_uris = ["spotify:track:" + track_id for track_id in recommended_tracks]
    sp.user_playlist_add_tracks(user=user, playlist_id=playlist['id'], tracks=track_uris)
    
    return

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
