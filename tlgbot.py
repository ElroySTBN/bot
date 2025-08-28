import os
import logging
import asyncio
import secrets
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# Configuration minimale
class Config:
    TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
    SUPPORT_PSEUDO = "Support Académique"
    MAX_SESSIONS = 100  # Limite mémoire
    SESSION_TIMEOUT = 1800  # 30 minutes
    MAX_FILES_PER_ORDER = 5  # Réduit

# Configuration du logging minimal
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING,  # Réduit les logs
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Modèles de données simplifiés
@dataclass
class UserSession:
    user_id: int
    step: str
    data: Dict[str, Any]
    created_at: datetime
    last_activity: datetime
    files: List[Dict] = field(default_factory=list)
    
    def add_file(self, file_id: str, file_name: str, file_size: int):
        if len(self.files) < Config.MAX_FILES_PER_ORDER:
            self.files.append({
                'file_id': file_id,
                'file_name': file_name,
                'file_size': file_size,
                'uploaded_at': datetime.now()
            })
            return True
        return False

@dataclass
class AcademicLevel:
    name: str
    emoji: str
    base_price: float

# Configuration académique compacte
class AcademicConfig:
    LEVELS = {
        'lycee': AcademicLevel("Lycée", "🎓", 18.0),
        'bachelor': AcademicLevel("Licence", "📚", 22.0),
        'master': AcademicLevel("Master", "🎯", 26.0),
        'phd': AcademicLevel("Doctorat", "🔬", 32.0)
    }
    
    DEADLINES = {
        '6h': ('Express - 6h', 1.8),
        '12h': ('Urgent - 12h', 1.7),
        '24h': ('Rapide - 24h', 1.5),
        '48h': ('Standard - 48h', 1.3),
        '3d': ('Normal - 3j', 1.2),
        '7d': ('Planifié - 7j', 1.0),
        '14d': ('Économique - 14j', 0.9)
    }

    CRYPTO = {
        'BTC': {'name': 'Bitcoin', 'emoji': '₿', 'address': 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh'},
        'ETH': {'name': 'Ethereum', 'emoji': 'Ξ', 'address': '0x742d35Cc6641C02e8743C1C5C1fEa8efCa7fA6B8'},
        'USDT': {'name': 'Tether', 'emoji': '₮', 'address': 'TQrZ2GZkfYZjqokhS7H2FfJ6AvL9vE8RbA'}
    }

# Gestionnaire de sessions en mémoire optimisé
class SessionManager:
    def __init__(self):
        self.sessions: Dict[int, UserSession] = {}
    
    def cleanup_old_sessions(self):
        """Nettoie les sessions expirées"""
        now = datetime.now()
        expired = [
            uid for uid, session in self.sessions.items()
            if now - session.last_activity > timedelta(seconds=Config.SESSION_TIMEOUT)
        ]
        for uid in expired:
            del self.sessions[uid]
    
    def get_session(self, user_id: int) -> Optional[UserSession]:
        # Nettoyage automatique
        if len(self.sessions) > Config.MAX_SESSIONS:
            self.cleanup_old_sessions()
        
        session = self.sessions.get(user_id)
        if session:
            session.last_activity = datetime.now()
        return session
    
    def create_session(self, user_id: int, step: str = 'menu') -> UserSession:
        session = UserSession(
            user_id=user_id,
            step=step,
            data={},
            created_at=datetime.now(),
            last_activity=datetime.now()
        )
        self.sessions[user_id] = session
        return session
    
    def update_session(self, user_id: int, step: str = None, data: Dict = None):
        session = self.get_session(user_id) or self.create_session(user_id)
        if step:
            session.step = step
        if data:
            session.data.update(data)
        session.last_activity = datetime.now()
    
    def clear_session(self, user_id: int):
        self.sessions.pop(user_id, None)

# Utilitaires
class Utils:
    @staticmethod
    def calculate_price(level: str, deadline: str, pages: int) -> float:
        level_data = AcademicConfig.LEVELS.get(level)
        if not level_data:
            return 0.0
        
        _, multiplier = AcademicConfig.DEADLINES.get(deadline, ('', 1.0))
        base_price = level_data.base_price * multiplier * pages
        return min(base_price, 50.0 * pages)
    
    @staticmethod
    def format_price(price: float) -> str:
        return f"{price:.2f}€"
    
    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes/1024:.1f} KB"
        else:
            return f"{size_bytes/(1024**2):.1f} MB"

# Interface utilisateur
class UI:
    @staticmethod
    def main_keyboard():
        return [
            [InlineKeyboardButton("📝 Nouvelle commande", callback_data="new_order")],
            [InlineKeyboardButton("💰 Tarification", callback_data="pricing"),
             InlineKeyboardButton("💬 Support", callback_data="support")],
            [InlineKeyboardButton("ℹ️ Informations", callback_data="info")]
        ]
    
    @staticmethod
    def back_keyboard():
        return [[InlineKeyboardButton("← Retour", callback_data="back"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu")]]
    
    @staticmethod
    def level_keyboard():
        buttons = []
        for key, level in AcademicConfig.LEVELS.items():
            buttons.append([InlineKeyboardButton(f"{level.emoji} {level.name}", callback_data=f"level_{key}")])
        buttons.extend(UI.back_keyboard())
        return buttons
    
    @staticmethod
    def deadline_keyboard():
        buttons = []
        for key, (label, _) in AcademicConfig.DEADLINES.items():
            buttons.append([InlineKeyboardButton(label, callback_data=f"deadline_{key}")])
        buttons.extend(UI.back_keyboard())
        return buttons
    
    @staticmethod
    def payment_keyboard():
        return [
            [InlineKeyboardButton("🏦 Virement bancaire", callback_data="payment_transfer")],
            [InlineKeyboardButton("₿ Cryptomonnaie", callback_data="payment_crypto")],
        ] + UI.back_keyboard()
    
    @staticmethod
    def crypto_keyboard():
        buttons = []
        for crypto, config in AcademicConfig.CRYPTO.items():
            buttons.append([InlineKeyboardButton(f"{config['emoji']} {crypto}", callback_data=f"crypto_{crypto}")])
        buttons.extend(UI.back_keyboard())
        return buttons

# Initialisation
session_manager = SessionManager()

# Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "📚 **EduMaster - Services Académiques**\n\n"
        "Plateforme de rédaction académique professionnelle.\n\n"
        "**Services :**\n"
        "• Rédaction de travaux académiques\n"
        "• Recherche et analyse\n"
        "• Révisions et corrections\n\n"
        "**Garanties :**\n"
        "• Travail original\n"
        "• Respect des délais\n"
        "• Support inclus"
    )
    
    keyboard = [[InlineKeyboardButton("Accéder au service", callback_data="menu")]]
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_manager.clear_session(user_id)
    
    menu_text = "🎯 **Menu Principal**\n\nSélectionnez l'action souhaitée :"
    keyboard = UI.main_keyboard()
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            menu_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            menu_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )

async def pricing_display(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pricing_text = "💰 **Grille Tarifaire**\n\n**Prix de base par page (350 mots) :**\n"
    
    for level in AcademicConfig.LEVELS.values():
        pricing_text += f"• {level.emoji} {level.name} : {level.base_price}€\n"
    
    pricing_text += (
        "\n**Multiplicateurs selon le délai :**\n"
        "• 14 jours : -10%\n• 7 jours : Prix standard\n• 3 jours : +20%\n"
        "• 48h : +30%\n• 24h : +50%\n• 12h : +70%\n• 6h : +80%\n\n"
        "*Prix maximum : 50€/page*"
    )
    
    keyboard = [
        [InlineKeyboardButton("📝 Passer commande", callback_data="new_order")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
    ]
    
    await update.callback_query.edit_message_text(
        pricing_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def info_display(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_text = (
        "ℹ️ **Informations**\n\n"
        "**Comment ça marche :**\n"
        "1. Décrivez votre projet\n"
        "2. Choisissez niveau et délai\n"
        "3. Envoyez vos fichiers (optionnel)\n"
        "4. Effectuez le paiement\n"
        "5. Recevez votre travail\n\n"
        "**Support :** Disponible 24h/24\n"
        "**Délais :** Respectés à 100%\n"
        "**Qualité :** Garantie satisfait ou remboursé"
    )
    
    keyboard = [
        [InlineKeyboardButton("📝 Commencer", callback_data="new_order")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
    ]
    
    await update.callback_query.edit_message_text(
        info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def start_order_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_manager.create_session(user_id, 'order_subject')
    
    order_text = (
        "📝 **Nouvelle Commande - Étape 1/6**\n\n"
        "**Décrivez le sujet de votre travail :**\n\n"
        "*Exemple : Analyse comparative des politiques monétaires européennes*\n\n"
        "Soyez aussi précis que possible."
    )
    
    await update.callback_query.edit_message_text(
        order_text, reply_markup=InlineKeyboardMarkup(UI.back_keyboard()), parse_mode=ParseMode.MARKDOWN
    )

async def support_interface(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_manager.update_session(user_id, 'support')
    
    support_text = (
        "💬 **Support Technique**\n\n"
        "Notre équipe est disponible 24h/24.\n\n"
        "**Tapez votre message ci-dessous :**"
    )
    
    keyboard = [[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]
    
    await update.callback_query.edit_message_text(
        support_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

# Handler principal pour les boutons
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    try:
        if data == "menu":
            await main_menu(update, context)
        elif data == "back":
            await main_menu(update, context)  # Simplifié
        elif data == "new_order":
            await start_order_flow(update, context)
        elif data == "pricing":
            await pricing_display(update, context)
        elif data == "info":
            await info_display(update, context)
        elif data == "support":
            await support_interface(update, context)
        elif data.startswith("level_"):
            await handle_level_selection(update, context, data)
        elif data.startswith("deadline_"):
            await handle_deadline_selection(update, context, data)
        elif data == "order_summary":
            await display_order_summary(update, context)
        elif data == "payment_transfer":
            await handle_payment_transfer(update, context)
        elif data == "payment_crypto":
            await handle_payment_crypto(update, context)
        elif data.startswith("crypto_"):
            await handle_crypto_selection(update, context, data)
        elif data == "skip_files":
            await display_order_summary(update, context)

    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        await query.edit_message_text(
            "⚠️ **Erreur temporaire**\n\nVeuillez réessayer.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Utilisateur"
    
    # Générer thread_id simple
    thread_id = hashlib.md5(f"{user_id}_{datetime.now().date()}".encode()).hexdigest()[:8]
    
    # Message à l'admin
    admin_message = (
        f"💬 **MESSAGE SUPPORT** - Thread #{thread_id}\n\n"
        f"**👤 Client :** @{username} (ID: {user_id})\n\n"
        f"**📝 Message :**\n{message_text}"
    )
    
    try:
        await context.bot.send_message(Config.ADMIN_ID, admin_message, parse_mode=ParseMode.MARKDOWN)
        
        confirmation = (
            f"✅ **Message envoyé avec succès**\n\n"
            f"**Référence :** #{thread_id}\n"
            f"**Temps de réponse :** Sous 2 heures\n\n"
            f"Notre équipe vous contactera rapidement."
        )
        
        keyboard = [[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]
        
        await update.message.reply_text(
            confirmation,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        
        session_manager.clear_session(user_id)
        
    except Exception as e:
        logger.error(f"Failed to send support message: {e}")
        await update.message.reply_text(
            "⚠️ **Erreur d'envoi**\n\nVeuillez réessayer plus tard.",
            parse_mode=ParseMode.MARKDOWN
        )

# Handler pour les fichiers
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)
    
    if not session or session.step != 'order_files':
        await update.message.reply_text(
            "⚠️ **Fichier non attendu**\n\nUtilisez le menu pour naviguer.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if len(session.files) >= Config.MAX_FILES_PER_ORDER:
        await update.message.reply_text(
            f"⚠️ **Limite atteinte**\n\nMaximum {Config.MAX_FILES_PER_ORDER} fichiers par commande.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        file_obj = None
        file_name = "fichier_sans_nom"
        file_size = 0
        
        if update.message.document:
            file_obj = update.message.document
            file_name = file_obj.file_name or "document"
            file_size = file_obj.file_size or 0
        elif update.message.photo:
            file_obj = update.message.photo[-1]
            file_name = f"image_{len(session.files) + 1}.jpg"
            file_size = file_obj.file_size or 0
        else:
            await update.message.reply_text(
                "⚠️ **Type de fichier non supporté**\n\nEnvoyez des documents ou des images.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Vérifier la taille (20MB max)
        if file_size > 20 * 1024 * 1024:
            await update.message.reply_text(
                "⚠️ **Fichier trop volumineux**\n\nTaille maximum : 20MB",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Ajouter le fichier à la session
        if session.add_file(file_obj.file_id, file_name, file_size):
            files_count = len(session.files)
            size_str = Utils.format_file_size(file_size)
            
            confirmation_text = (
                f"✅ **Fichier ajouté**\n\n"
                f"📎 {file_name} ({size_str})\n\n"
                f"**Total :** {files_count}/{Config.MAX_FILES_PER_ORDER} fichiers\n\n"
                f"Vous pouvez envoyer d'autres fichiers ou continuer."
            )
            
            keyboard = [
                [InlineKeyboardButton("✅ Continuer vers le récapitulatif", callback_data="order_summary")]
            ]
            
            await update.message.reply_text(
                confirmation_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
    except Exception as e:
        logger.error(f"Error in file upload: {e}")
        await update.message.reply_text(
            "⚠️ **Erreur lors de l'envoi**\n\nVeuillez réessayer.",
            parse_mode=ParseMode.MARKDOWN
        )

# Commande admin pour répondre au support
async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_ID:
        return
    
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "**Format :** `/reply user_id message`\n"
                "**Exemple :** `/reply 123456789 Bonjour, comment puis-je vous aider ?`"
            )
            return
        
        user_id = int(args[0])
        admin_message = ' '.join(args[1:])
        
        # Message à l'utilisateur
        user_response = f"💬 **{Config.SUPPORT_PSEUDO}**\n\n{admin_message}"
        
        await context.bot.send_message(user_id, user_response, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(f"✅ **Réponse envoyée** à l'utilisateur {user_id}")
        
    except ValueError:
        await update.message.reply_text("⚠️ **ID utilisateur invalide**")
    except Exception as e:
        logger.error(f"Admin reply error: {e}")
        await update.message.reply_text(f"⚠️ **Erreur :** {e}")

def main():
    """Fonction principale"""
    if not Config.TOKEN:
        logger.error("TOKEN manquant ! Définissez BOT_TOKEN")
        return
    
    if Config.ADMIN_ID == 0:
        logger.error("ADMIN_ID manquant ! Définissez ADMIN_ID")
        return
    
    app = Application.builder().token(Config.TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", main_menu))
    app.add_handler(CommandHandler("reply", admin_reply))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    logger.warning("🚀 EduMaster Bot optimisé démarré")
    logger.warning(f"👤 Admin ID : {Config.ADMIN_ID}")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()uillez réessayer.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_level_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    level_key = data.replace("level_", "")
    user_id = update.effective_user.id
    
    session_manager.update_session(user_id, 'order_pages', {'level': level_key})
    level = AcademicConfig.LEVELS[level_key]
    
    pages_text = (
        f"📝 **Nouvelle Commande - Étape 3/6**\n\n"
        f"**Niveau sélectionné :** {level.emoji} {level.name}\n"
        f"**Prix de base :** {level.base_price}€/page\n\n"
        f"**Indiquez le nombre de pages souhaitées :**\n"
        f"*(Une page = environ 350 mots)*"
    )
    
    await update.callback_query.edit_message_text(
        pages_text, reply_markup=InlineKeyboardMarkup(UI.back_keyboard()), parse_mode=ParseMode.MARKDOWN
    )

async def handle_deadline_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    deadline_key = data.replace("deadline_", "")
    user_id = update.effective_user.id
    
    session = session_manager.get_session(user_id)
    session_manager.update_session(user_id, 'order_instructions', {'deadline': deadline_key})
    
    # Calcul du prix final
    final_price = Utils.calculate_price(
        session.data.get('level'),
        deadline_key,
        session.data.get('pages', 1)
    )
    
    session_manager.update_session(user_id, data={'final_price': final_price})
    
    instructions_text = (
        f"📋 **Nouvelle Commande - Étape 5/6**\n\n"
        f"**Consignes et instructions complémentaires**\n\n"
        f"Tapez toutes les informations importantes :\n"
        f"• Format requis (APA, MLA, etc.)\n"
        f"• Nombre de sources minimum\n"
        f"• Consignes spécifiques\n\n"
        f"*Si vous n'avez pas d'instructions, tapez \"aucune\"*"
    )
    
    await update.callback_query.edit_message_text(
        instructions_text, reply_markup=InlineKeyboardMarkup(UI.back_keyboard()), parse_mode=ParseMode.MARKDOWN
    )

async def display_order_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)
    
    if not session:
        await update.callback_query.edit_message_text(
            "⚠️ **Session expirée**\n\nVeuillez recommencer.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    level = AcademicConfig.LEVELS[session.data.get('level')]
    deadline_label = AcademicConfig.DEADLINES[session.data.get('deadline')][0]
    files_count = len(session.files)
    
    summary_text = (
        f"📋 **Récapitulatif de votre commande**\n\n"
        f"**Sujet :** {session.data.get('subject', 'Non défini')}\n"
        f"**Niveau :** {level.emoji} {level.name}\n"
        f"**Pages :** {session.data.get('pages')} page(s)\n"
        f"**Délai :** {deadline_label}\n"
        f"**Instructions :** {session.data.get('instructions_text', 'Aucune')[:50]}...\n"
        f"**Fichiers joints :** {files_count} document(s)\n\n"
        f"**💰 Prix total :** {Utils.format_price(session.data.get('final_price', 0))}\n\n"
        f"Choisissez votre méthode de paiement :"
    )
    
    keyboard = UI.payment_keyboard()
    
    await update.callback_query.edit_message_text(
        summary_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def handle_payment_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_payment_info(update, context, 'transfer')

async def handle_payment_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    crypto_text = "₿ **Paiement Cryptomonnaie**\n\nSélectionnez votre cryptomonnaie :"
    await update.callback_query.edit_message_text(
        crypto_text, reply_markup=InlineKeyboardMarkup(UI.crypto_keyboard()), parse_mode=ParseMode.MARKDOWN
    )

async def handle_crypto_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    crypto = data.replace("crypto_", "")
    await send_payment_info(update, context, crypto)

async def send_payment_info(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_type: str):
    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)
    
    if not session:
        return
    
    # Générer numéro de commande unique
    order_number = f"EDU{secrets.token_hex(4).upper()}"
    
    if payment_type == 'transfer':
        payment_text = (
            f"🏦 **Paiement par Virement Bancaire**\n\n"
            f"**Commande #{order_number}**\n\n"
            f"**Coordonnées bancaires :**\n"
            f"• IBAN : FR76 1234 5678 9012 3456 7890 123\n"
            f"• BIC : SOGEFRPP\n"
            f"• Titulaire : EduMaster Services\n"
            f"• Banque : Société Générale\n\n"
            f"**Montant exact :** {Utils.format_price(session.data.get('final_price', 0))}\n\n"
            f"**⚠️ IMPORTANT :**\n"
            f"• Indiquez en référence : {order_number}\n"
            f"• Conservez votre reçu bancaire\n"
            f"• Validation sous 24-48h ouvrés"
        )
    else:
        crypto_config = AcademicConfig.CRYPTO[payment_type]
        payment_text = (
            f"₿ **Paiement {crypto_config['name']} {crypto_config['emoji']}**\n\n"
            f"**Commande #{order_number}**\n\n"
            f"**Adresse de paiement :**\n"
            f"`{crypto_config['address']}`\n\n"
            f"**Montant exact :** {Utils.format_price(session.data.get('final_price', 0))}\n\n"
            f"**⚠️ IMPORTANT :**\n"
            f"• Envoyez le montant EXACT\n"
            f"• Conservez votre hash de transaction\n"
            f"• Validation automatique sous 30 min"
        )
    
    keyboard = [
        [InlineKeyboardButton("✅ Paiement effectué", callback_data="payment_done")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
    ]
    
    await update.callback_query.edit_message_text(
        payment_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )
    
    # Envoyer notification admin
    await send_admin_notification(context, update.effective_user, session, order_number, payment_type)
    
    # Clear session
    session_manager.clear_session(user_id)

async def send_admin_notification(context, user, session, order_number, payment_type):
    try:
        level_name = AcademicConfig.LEVELS[session.data.get('level')].name
        deadline_label = AcademicConfig.DEADLINES[session.data.get('deadline')][0]
        files_count = len(session.files)
        
        payment_emoji = "🏦" if payment_type == 'transfer' else "₿"
        payment_name = "Virement bancaire" if payment_type == 'transfer' else f"Crypto ({payment_type})"
        
        admin_notification = (
            f"🆕 **NOUVELLE COMMANDE #{order_number}**\n\n"
            f"**👤 Client :** @{user.username or 'Sans username'} (ID: {user.id})\n\n"
            f"**📋 Détails :**\n"
            f"• **Sujet :** {session.data.get('subject')}\n"
            f"• **Niveau :** {level_name}\n"
            f"• **Pages :** {session.data.get('pages')}\n"
            f"• **Délai :** {deadline_label}\n"
            f"• **Prix :** {Utils.format_price(session.data.get('final_price', 0))}\n"
            f"• **Paiement :** {payment_emoji} {payment_name}\n"
            f"• **Fichiers joints :** {files_count} document(s)\n\n"
        )
        
        if session.data.get('instructions_text') and session.data.get('instructions_text').lower() != 'aucune':
            admin_notification += f"**📝 Instructions :**\n{session.data.get('instructions_text')}\n\n"
        
        admin_notification += f"⏳ *En attente de paiement...*"
        
        await context.bot.send_message(Config.ADMIN_ID, admin_notification, parse_mode=ParseMode.MARKDOWN)
        
        # Envoyer les fichiers joints s'il y en a
        for i, file_data in enumerate(session.files, 1):
            try:
                file_caption = f"📎 **Fichier {i}/{files_count}** - {order_number}\n{file_data['file_name']}"
                await context.bot.send_document(
                    Config.ADMIN_ID,
                    document=file_data['file_id'],
                    caption=file_caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to send file: {e}")
                
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

# Handler pour les messages texte
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = session_manager.get_session(user_id)
    message_text = update.message.text

    if not session:
        await update.message.reply_text(
            "🤔 **Navigation perdue ?**\n\nUtilisez le menu ci-dessous :",
            reply_markup=InlineKeyboardMarkup(UI.main_keyboard()),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        step = session.step

        if step == 'order_subject':
            session_manager.update_session(user_id, 'order_level', {'subject': message_text})
            
            subject_confirmation = (
                f"📝 **Nouvelle Commande - Étape 2/6**\n\n"
                f"**Sujet enregistré :**\n*{message_text}*\n\n"
                f"Sélectionnez votre niveau académique :"
            )
            
            await update.message.reply_text(
                subject_confirmation,
                reply_markup=InlineKeyboardMarkup(UI.level_keyboard()),
                parse_mode=ParseMode.MARKDOWN
            )
            
        elif step == 'order_pages':
            try:
                pages = int(message_text.strip())
                if pages <= 0 or pages > 50:
                    raise ValueError()
                
                session_manager.update_session(user_id, 'order_deadline', {'pages': pages})
                
                pages_confirmation = (
                    f"📝 **Nouvelle Commande - Étape 4/6**\n\n"
                    f"**{pages} page(s) confirmée(s)**\n\n"
                    f"Sélectionnez votre délai de livraison :"
                )
                
                await update.message.reply_text(
                    pages_confirmation,
                    reply_markup=InlineKeyboardMarkup(UI.deadline_keyboard()),
                    parse_mode=ParseMode.MARKDOWN
                )
                
            except ValueError:
                await update.message.reply_text(
                    "⚠️ **Format incorrect**\n\nEntrez un nombre entre 1 et 50.\n*Exemple : 5*",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        elif step == 'order_instructions':
            session_manager.update_session(user_id, 'order_files', {'instructions_text': message_text})
            
            files_text = (
                f"📎 **Nouvelle Commande - Étape 6/6**\n\n"
                f"**Documents et ressources (optionnel)**\n\n"
                f"Vous pouvez :\n"
                f"• Envoyer des fichiers (PDF, DOC, images)\n"
                f"• Passer directement au récapitulatif\n\n"
                f"**Fichiers envoyés :** 0/{Config.MAX_FILES_PER_ORDER}"
            )
            
            keyboard = [
                [InlineKeyboardButton("↩ Passer cette étape", callback_data="skip_files")],
                UI.back_keyboard()[0]
            ]
            
            await update.message.reply_text(
                files_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            
        elif step == 'support':
            await handle_support_message(update, context, message_text)

    except Exception as e:
        logger.error(f"Error in text handler: {e}")
        await update.message.reply_text(
            "⚠️ **Erreur temporaire**\n\nVe
