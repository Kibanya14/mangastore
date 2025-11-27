from backend.apps import create_app

# Point d'entrée Gunicorn / Render
app = create_app()

if __name__ == "__main__":
    # Lancement local éventuel (non utilisé par Render)
    from backend.apps import socketio
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
