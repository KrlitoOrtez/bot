import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    JobQueue,
)
import json
from datetime import datetime
import pytz
import logging

# Set up logging
logging.basicConfig(
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger('LotteryBot')

# Configuration
TELEGRAM_BOT_TOKEN = "7659875840:AAH-iqhqY6I4T0OF3uAgaITmlkGQR2zACIk"
BLINK_API_TOKEN = "blink_857EaaE6N5RvqvkVOkfaAqd1cj65RXYmlYdV6ItrUOHr26aCOc0k8rugKssM6Rru"
BLINK_API_URL = "https://api.blink.sv/graphql"
BTC_WALLET_ID = "49cb9756-271a-4328-8527-4bd5d0ffac75"

# Set timezone to CST
CST = pytz.timezone('America/Tegucigalpa')
DRAW_TIMES = ["11:00", "15:00", "21:00"]

# In-memory database for bets and pending payments
bets = {}
pending_payments = {}

def create_lightning_invoice(amount, memo="Lottery Bet"):
    """Generate Lightning invoice using Blink"""
    query = """
    mutation LnInvoiceCreate($input: LnInvoiceCreateInput!) {
        lnInvoiceCreate(input: $input) {
            invoice {
                paymentRequest
                paymentHash
                paymentSecret
                satoshis
            }
            errors {
                message
            }
        }
    }
    """
    variables = {
        "input": {
            "amount": amount,
            "walletId": BTC_WALLET_ID,
            "memo": memo
        }
    }
    headers = {
        "content-type": "application/json",
        "X-API-KEY": BLINK_API_TOKEN
    }
    
    try:
        response = requests.post(
            BLINK_API_URL,
            json={"query": query, "variables": variables},
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            invoice_data = data.get("data", {}).get("lnInvoiceCreate", {}).get("invoice", None)
            if invoice_data:
                return invoice_data["paymentRequest"], invoice_data["paymentHash"]
            logger.error(f"Error creating invoice: {data}")
            return None, None
        else:
            logger.error(f"Error response from Blink API: {response.status_code} - {response.text}")
            return None, None
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        return None, None

def check_payment(payment_hash):
    """Verify payment status using Blink API"""
    query = """
    query GetInvoice($hash: String!) {
        getLightningInvoice(hash: $hash) {
            settled
            settleDate
        }
    }
    """
    variables = {"hash": payment_hash}
    headers = {
        "content-type": "application/json",
        "X-API-KEY": BLINK_API_TOKEN
    }
    
    try:
        response = requests.post(
            BLINK_API_URL,
            json={"query": query, "variables": variables},
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            invoice = data.get("data", {}).get("getLightningInvoice", {})
            return invoice.get("settled", False)
        return False
    except Exception as e:
        logger.error(f"Error checking payment: {e}")
        return False

async def start(update: Update, context: CallbackContext):
    """Handle the /start command"""
    welcome_message = (
        "🎮 ¡Bienvenido a la Lotería Lightning! 🎮\n\n"
        "Cómo jugar:\n"
        "1. Elige un número del 00 al 99\n"
        "2. Apuesta entre 50 y 1000 sats\n"
        "3. Si ganas, ¡recibes 70 veces tu apuesta!\n\n"
        "Horarios de sorteo (Honduras):\n"
        "• 11:00 AM\n"
        "• 3:00 PM\n"
        "• 9:00 PM\n\n"
        "Usa /apostar para comenzar a jugar.\n"
        "Usa /reglas para ver las reglas completas."
    )
    await update.message.reply_text(welcome_message)

async def apostar(update: Update, context: CallbackContext):
    """Handle the /apostar command"""
    current_time = datetime.now(CST)
    current_time_str = current_time.strftime("%H:%M")
    
    next_draw = None
    for draw_time in DRAW_TIMES:
        if current_time_str < draw_time:
            next_draw = draw_time
            break
    if not next_draw:
        next_draw = DRAW_TIMES[0]
    
    keyboard = []
    for start in range(0, 100, 8):
        row = []
        for i in range(start, min(start + 8, 100)):
            row.append(InlineKeyboardButton(
                f"{i:02d}",
                callback_data=f"bet_{i:02d}"
            ))
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Elige tu número para el próximo sorteo:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: CallbackContext):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("bet_"):
        chosen_number = query.data.split("_")[1]
        context.user_data["chosen_number"] = chosen_number
        
        keyboard = [
            [
                InlineKeyboardButton("50 sats", callback_data="amount_50"),
                InlineKeyboardButton("100 sats", callback_data="amount_100"),
                InlineKeyboardButton("500 sats", callback_data="amount_500")
            ],
            [InlineKeyboardButton("💰 Monto Personalizado", callback_data="amount_custom")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"Has elegido el número {chosen_number}.\n"
            f"Selecciona el monto a apostar:",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith("amount_"):
        amount_data = query.data.split("_")[1]
        chosen_number = context.user_data.get("chosen_number")
        
        if amount_data == "custom":
            context.user_data["awaiting_custom_amount"] = True
            await query.edit_message_text(
                f"Has elegido el número {chosen_number}.\n"
                "Por favor, ingresa el monto en sats (mínimo 50, máximo 1000):"
            )
            return
            
        try:
            amount = int(amount_data)
            await process_bet(query.message, context, chosen_number, amount)
        except Exception as e:
            logger.error(f"Error processing bet: {e}")
            await query.edit_message_text(
                "❌ Error procesando la apuesta. Por favor intenta nuevamente con /apostar"
            )

async def handle_custom_amount(update: Update, context: CallbackContext):
    """Handle custom amount messages"""
    if not context.user_data.get("awaiting_custom_amount"):
        return

    try:
        amount = int(update.message.text)
        if amount < 50 or amount > 1000:
            await update.message.reply_text(
                "❌ El monto debe estar entre 50 y 1000 sats. Por favor intenta nuevamente:"
            )
            return

        chosen_number = context.user_data.get("chosen_number")
        if not chosen_number:
            await update.message.reply_text(
                "❌ Error: No se encontró el número seleccionado. Por favor usa /apostar nuevamente."
            )
            return

        context.user_data["awaiting_custom_amount"] = False
        await process_bet(update.message, context, chosen_number, amount)
        
    except ValueError:
        await update.message.reply_text(
            "❌ Por favor ingresa un número válido entre 50 y 1000 sats:"
        )

async def process_bet(message, context: CallbackContext, chosen_number: str, amount: int):
    """Process bet with given amount"""
    try:
        telegram_user = message.chat.username if message.chat.username else "N/A"
        memo = f"Apuesta {amount} sats al {chosen_number} por @{telegram_user}"
        invoice, payment_hash = create_lightning_invoice(amount, memo)
        
        if invoice and payment_hash:
            user_id = message.chat.id
            username = message.chat.username or str(user_id)
            
            bet_info = {
                "user_id": user_id,
                "username": username,
                "telegram_user": telegram_user,  # Agregar el nombre de usuario de Telegram
                "amount": amount,
                "payment_hash": payment_hash,
                "timestamp": datetime.now(CST)
            }
            
            pending_payments[payment_hash] = {
                "bet_info": bet_info,
                "number": chosen_number
            }
            
            expiry_time = datetime.now(CST).timestamp() + 3600

            # Send invoice information
            info_message = await message.reply_text(
                f"📝 Número: {chosen_number}\n"
                f"💰 Monto: {amount} sats\n"
                f"⏳ Expira en: 60 minutos\n\n"
                "La apuesta será registrada al confirmar el pago."
            )

            invoice_message = await message.reply_text(
                "⚡ Invoice para copiar:\n\n"
                f"{invoice}"
            )

            await message.reply_text(
                "👆 Toca el código de arriba para copiarlo\n"
                "Una vez que pagues, tu apuesta quedará registrada automáticamente."
            )
            
            context.job_queue.run_once(
                check_pending_payment,
                30,
                data={
                    "payment_hash": payment_hash,
                    "chat_id": message.chat_id,
                    "message_id": info_message.message_id,
                    "invoice_message_id": invoice_message.message_id,
                    "expiry_time": expiry_time
                }
            )
        else:
            await message.reply_text(
                "❌ Error al generar la factura. Por favor intenta nuevamente con /apostar"
            )
    except Exception as e:
        logger.error(f"Error processing bet: {e}")
        await message.reply_text(
            "❌ Ocurrió un error. Por favor intenta nuevamente con /apostar"
        )

async def check_pending_payment(context: CallbackContext):
    """Check if a pending payment was completed"""
    job_data = context.job.data
    payment_hash = job_data["payment_hash"]
    expiry_time = job_data.get("expiry_time")
    current_time = datetime.now(CST).timestamp()
    
    if payment_hash in pending_payments:
        if expiry_time and current_time > expiry_time:
            try:
                await context.bot.edit_message_text(
                    "⏰ La factura ha expirado. Por favor genera una nueva apuesta con /apostar",
                    chat_id=job_data["chat_id"],
                    message_id=job_data["message_id"]
                )
                del pending_payments[payment_hash]
            except Exception as e:
                logger.error(f"Error updating expired message: {e}")
            return
            
        if check_payment(payment_hash):
            payment_info = pending_payments[payment_hash]
            number = payment_info["number"]
            bet_info = payment_info["bet_info"]
            
            if number not in bets:
                bets[number] = []
            bets[number].append(bet_info)
            
            try:
                await context.bot.edit_message_text(
                    "✅ ¡Pago confirmado! Tu apuesta ha sido registrada.",
                    chat_id=job_data["chat_id"],
                    message_id=job_data["message_id"]
                )
                del pending_payments[payment_hash]
            except Exception as e:
                logger.error(f"Error updating confirmation message: {e}")
        else:
            if expiry_time:
                remaining_minutes = int((expiry_time - current_time) / 60)
                if remaining_minutes > 0:
                    context.job_queue.run_once(
                        check_pending_payment,
                        30,
                        data=job_data
                    )
                    
                    try:
                        await context.bot.edit_message_text(
                            f"⏳ Esperando pago... Expira en {remaining_minutes} minutos.\n"
                            "Si ya pagaste, usa /verify seguido del hash del pago.",
                            chat_id=job_data["chat_id"],
                            message_id=job_data["message_id"]
                        )
                    except Exception as e:
                        logger.error(f"Error updating status message: {e}")
                else:
                    try:
                        await context.bot.edit_message_text(
                            "⏰ La factura ha expirado. Por favor genera una nueva apuesta con /apostar",
                            chat_id=job_data["chat_id"],
                            message_id=job_data["message_id"]
                        )
                        del pending_payments[payment_hash]
                    except Exception as e:
                        logger.error(f"Error updating expired message: {e}")

async def run_draw(context: CallbackContext):
    """Process the lottery draw"""
    current_time = datetime.now(CST)
    current_time_str = current_time.strftime("%H:%M")
    
    if current_time_str in DRAW_TIMES:
        try:
            response = requests.get("https://blockchain.info/latestblock")
            response.raise_for_status()
            latest_block = response.json()
            block_hash = latest_block["hash"]
            winning_number = f"{int(block_hash[-2:], 16) % 100:02d}"
            
            winners = bets.get(winning_number, [])
            total_payout = sum(winner["amount"] * 70 for winner in winners)
            
            message = (
                f"🎲 Resultado del Sorteo {current_time_str} CST 🎲\n\n"
                f"🎯 Número ganador: {winning_number}\n"
                f"🔍 Block hash: {block_hash}\n\n"
            )
            
            if winners:
                message += "🏆 Ganadores:\n"
                for winner in winners:
                    payout = winner["amount"] * 70
                    message += f"@{winner['telegram_user']}: {payout} sats\n"  # Mostrar nombre de usuario de Telegram
            else:
                message += "😢 No hubo ganadores en este sorteo.\n"
            
            message += f"\n💰 Pago total: {total_payout} sats"
            
            logger.info(f"Resultados del sorteo: {message}")
            
            try:
                await context.bot.send_message(
                    chat_id="@diasatsbot",
                    text=message
                )
                logger.info("Resultados enviados al canal exitosamente")
            except Exception as e:
                logger.error(f"Error enviando resultados al canal: {e}")
            
            # Limpiar apuestas para la siguiente ronda
            bets.clear()
            logger.info("Apuestas limpiadas para la siguiente ronda")
            
        except Exception as e:
            logger.error(f"Error en el sorteo: {e}")
            error_message = (
                "❌ Error al procesar el sorteo. "
                "Por favor contacta al administrador."
            )
            try:
                await context.bot.send_message(
                    chat_id="@diasatsbot",
                    text=error_message
                )
            except Exception as send_error:
                logger.error(f"Error enviando mensaje de error: {send_error}")

async def reglas(update: Update, context: CallbackContext):
    """Handle the /reglas command"""
    rules_message = (
        "📜 Reglas de la Lotería Lightning 📜\n\n"
        "1. Elige un número del 00 al 99.\n"
        "2. Apuesta entre 50 y 1000 sats.\n"
        "3. Si ganas, ¡recibes 70 veces tu apuesta!\n"
        "4. Los sorteos se realizan a las 9:00 AM, 3:00 PM y 9:00 PM CST.\n"
        "5. Solo se aceptan apuestas pagadas antes del sorteo.\n"
        "6. Las apuestas no son reembolsables.\n\n"
        "¡Buena suerte!"
    )
    await update.message.reply_text(rules_message)

async def verify_payment(update: Update, context: CallbackContext):
    """Handle the /verify command"""
    if len(context.args) != 1:
        await update.message.reply_text("❌ Uso: /verify <payment_hash>")
        return
    
    payment_hash = context.args[0]
    if payment_hash in pending_payments:
        if check_payment(payment_hash):
            payment_info = pending_payments[payment_hash]
            number = payment_info["number"]
            bet_info = payment_info["bet_info"]
            
            if number not in bets:
                bets[number] = []
            bets[number].append(bet_info)
            
            await update.message.reply_text("✅ ¡Pago confirmado! Tu apuesta ha sido registrada.")
            del pending_payments[payment_hash]
        else:
            await update.message.reply_text("❌ Pago no encontrado o pendiente. Por favor intenta nuevamente.")
    else:
        await update.message.reply_text("❌ No se encontró el hash del pago en las apuestas pendientes.")

async def status(update: Update, context: CallbackContext):
    """Handle the /status command"""
    total_bets = sum(len(bet_list) for bet_list in bets.values())
    total_amount = sum(bet_info["amount"] for bet_list in bets.values() for bet_info in bet_list)
    
    status_message = (
        "📊 Estado de la Lotería 📊\n\n"
        f"Total de apuestas: {total_bets}\n"
        f"Total apostado: {total_amount} sats\n"
    )
    await update.message.reply_text(status_message)

def main():
    """Inicializar y ejecutar el bot"""
    try:
        # Inicializar la aplicación
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Configurar job queue
        job_queue = application.job_queue
        
        # Programar sorteos
        job_queue.run_repeating(run_draw, interval=60, first=0)
        
        # Agregar handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("apostar", apostar))
        application.add_handler(CommandHandler("reglas", reglas))
        application.add_handler(CommandHandler("verify", verify_payment))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE,
            handle_custom_amount
        ))
        
        # Iniciar el bot
        logger.info("Bot iniciado exitosamente")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Error iniciando el bot: {e}")
        raise

if __name__ == "__main__":
    main()
