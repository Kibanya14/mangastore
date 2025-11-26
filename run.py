import os
import sys
import getpass
from dotenv import load_dotenv

# Chemins du projet (root = dossier contenant ce fichier)
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

load_dotenv()

def create_directories():
    """Crée les dossiers nécessaires"""
    directories = [
        os.path.join(project_root, 'frontend', 'static', 'uploads', 'logos'),
        os.path.join(project_root, 'frontend', 'static', 'uploads', 'profiles'),
        os.path.join(project_root, 'frontend', 'static', 'uploads', 'products'),
        os.path.join(project_root, 'frontend', 'static', 'uploads', 'categories'),
        os.path.join(project_root, 'frontend', 'templates', 'client'),
        os.path.join(project_root, 'frontend', 'templates', 'admin'),
        os.path.join(project_root, 'frontend', 'templates', 'errors'),
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"✅ Dossier créé: {directory}")
        except Exception as e:
            print(f"⚠️ Erreur création {directory}: {e}")

def create_super_admin_interactive():
    """Crée le super admin de manière interactive"""
    print("\n" + "="*60)
    print("👑 CRÉATION DU SUPER ADMINISTRATEUR")
    print("="*60)
    
    while True:
        first_name = input("Prénom du super admin: ").strip()
        if first_name:
            break
        print("❌ Le prénom est obligatoire")
    
    while True:
        last_name = input("Nom du super admin: ").strip()
        if last_name:
            break
        print("❌ Le nom est obligatoire")
    
    while True:
        email = input("Email du super admin: ").strip()
        if email and '@' in email:
            break
        print("❌ Email invalide")
    
    while True:
        password = getpass.getpass("Mot de passe du super admin: ").strip()
        if len(password) >= 6:
            confirm_password = getpass.getpass("Confirmer le mot de passe: ").strip()
            if password == confirm_password:
                break
            else:
                print("❌ Les mots de passe ne correspondent pas")
        else:
            print("❌ Le mot de passe doit faire au moins 6 caractères")
    
    return {
        'first_name': first_name,
        'last_name': last_name,
        'email': email,
        'password': password
    }

def main():
    print("🚀 Démarrage de Manga Store...")
    
    # Créer les dossiers nécessaires
    create_directories()
    
    # Imports après configuration
    from backend.apps import create_app
    from backend.models import db, User, ShopSettings
    
    # Créer l'application
    app = create_app()
    
    # Initialisation de la base de données
    with app.app_context():
        try:
            db.create_all()
            print("✅ Tables de la base de données créées")
            
            # Vérifier s'il existe déjà un super admin
            existing_super_admin = User.query.filter_by(is_super_admin=True).first()
            
            if not existing_super_admin:
                # Créer le super admin interactivement
                admin_data = create_super_admin_interactive()
                
                super_admin = User(
                    email=admin_data['email'],
                    first_name=admin_data['first_name'],
                    last_name=admin_data['last_name'],
                    is_admin=True,
                    is_super_admin=True
                )
                super_admin.set_password(admin_data['password'])
                db.session.add(super_admin)
                
                # Créer les paramètres par défaut
                settings = ShopSettings()
                db.session.add(settings)
                
                db.session.commit()
                
                print("\n" + "="*60)
                print("🎉 SUPER ADMIN CRÉÉ AVEC SUCCÈS!")
                print("="*60)
                print(f"👤 Nom: {admin_data['first_name']} {admin_data['last_name']}")
                print(f"📧 Email: {admin_data['email']}")
                print("🔑 Mot de passe: ********")
                print("="*60)
                
                # Envoyer un email de bienvenue
                try:
                    from flask_mail import Message
                    mail = app.extensions['mail']
                    msg = Message(
                        subject='🎉 Bienvenue sur Manga Store - Super Admin',
                        sender=app.config['MAIL_DEFAULT_SENDER'],
                        recipients=[admin_data['email']]
                    )
                    
                    html_content = """
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <style>
                            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                            .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                            .info-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #667eea; }}
                            .footer {{ text-align: center; margin-top: 30px; color: #666; font-size: 14px; }}
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="header">
                                <h1>🎉 Bienvenue sur Manga Store</h1>
                                <p>Votre boutique en ligne est maintenant opérationnelle!</p>
                            </div>
                            <div class="content">
                                <h2>Bonjour {first_name},</h2>
                                <p>Votre compte Super Administrateur a été créé avec succès.</p>
                                
                                <div class="info-box">
                                    <h3>📋 Vos informations de connexion:</h3>
                                    <p><strong>Email:</strong> {email}</p>
                                    <p><strong>Rôle:</strong> Super Administrateur</p>
                                    <p><strong>Accès complet:</strong> Gestion produits, commandes, administrateurs et paramètres</p>
                                </div>
                                
                                <div class="info-box">
                                    <h3>🔗 Liens importants:</h3>
                                    <p><strong>Administration:</strong> http://localhost:5000/admin</p>
                                    <p><strong>Boutique:</strong> http://localhost:5000</p>
                                </div>
                                
                                <div class="info-box">
                                    <h3>🚀 Premières actions recommandées:</h3>
                                    <p>1. Configurer les paramètres de votre boutique</p>
                                    <p>2. Ajouter vos premiers produits</p>
                                    <p>3. Configurer les méthodes de paiement</p>
                                    <p>4. Inviter d'autres administrateurs si nécessaire</p>
                                </div>
                                
                                <p>Nous sommes ravis de vous accompagner dans votre projet e-commerce!</p>
                            </div>
                            <div class="footer">
                                            <p>© {datetime_now} Manga Store. Tous droits réservés.</p>
                                            <p>Propulsé par Esperdigi</p>
                                        </div>
                        </div>
                    </body>
                    </html>
                    """
                    
                    # Inject dynamic values into the email template
                    try:
                        from datetime import datetime as _dt
                        year = str(_dt.now().year)
                    except Exception:
                        year = '2025'

                    html_content = html_content.format(first_name=admin_data['first_name'], email=admin_data['email'], datetime_now=year)
                    msg.html = html_content
                    
                    msg.body = f"""
                    Bonjour {admin_data['first_name']},
                    
                    Félicitations ! Votre boutique Manga Store est maintenant opérationnelle.
                    
                    VOTRE COMPTE SUPER ADMINISTRATEUR:
                    Email: {admin_data['email']}
                    Rôle: Super Administrateur
                    
                    ACCÈS ADMINISTRATION:
                    URL: http://localhost:5000/admin
                    
                    BOUTIQUE CLIENT:
                    URL: http://localhost:5000
                    
                    PREMIÈRES ÉTAPES:
                    1. Connectez-vous à l'administration
                    2. Configurez les paramètres de votre boutique
                    3. Ajoutez vos premiers produits
                    4. Configurez les méthodes de paiement
                    
                    Nous sommes là pour vous accompagner !
                    
                    Cordialement,
                    L'équipe Manga Store
                    """
                    
                    mail.send(msg)
                    print("📧 Email de bienvenue envoyé avec succès!")
                    
                except Exception as e:
                    print(f"⚠️ Email non envoyé (configuration SMTP à vérifier): {e}")
                    print("💡 Conseil: Vérifiez votre configuration SMTP dans le fichier .env")
                    
            else:
                print("✅ Super admin existe déjà")
                print(f"👤 Connectez-vous avec: {existing_super_admin.email}")
                
        except Exception as e:
            print(f"❌ Erreur initialisation: {e}")
            return

    # Afficher la localisation réelle de la base de données configurée
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    try:
        # Extraire le chemin local si sqlite
        if db_uri and db_uri.startswith('sqlite:'):
            path = db_uri.replace('sqlite:///', '')
        else:
            path = db_uri
    except Exception:
        path = 'database.db'
    print(f"💾 Base de données: {path}")

    # Informations de lancement
    print("\n" + "="*50)
    print("🎯 MANGA STORE - PRÊT À FONCTIONNER!")
    print("="*50)
    print("🌐 URL Client: http://localhost:5000")
    print("⚙️  URL Admin: http://localhost:5000/admin")
    print("🚚 URL Livreur: http://localhost:5000/livreur")
    print("📧 Emails: Activés avec Gmail SMTP")
    print("="*50)
    print("\nAppuyez sur Ctrl+C pour arrêter le serveur\n")
    
    # Démarrer le serveur (SocketIO pour la signalisation)
    try:
        from backend.apps import socketio
        socketio.run(app, debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n⏹️ Serveur arrêté par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur: {e}")

if __name__ == '__main__':
    main()
