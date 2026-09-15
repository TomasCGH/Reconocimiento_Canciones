import os
import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from scipy.fft import fft
import tkinter as tk
from tkinter import simpledialog, messagebox
from PIL import Image, ImageTk
import pyttsx3
import threading
from playsound import playsound
import time
import multiprocessing

#Base directory absolute path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DURACION = 12
RATE = 44100
CHUNK_TOP = 40
UMBRAL_COINCIDENCIAS = 0.45
BASE_PATH = os.path.join(BASE_DIR, "base_de_canciones")
HUELLAS_PATH = os.path.join(BASE_DIR, "huellas")
FONDOS_PATH = os.path.join(BASE_DIR, "audio_fondos")
FONDO_IMAGEN = os.path.join(BASE_DIR, "fondo.png")
VOLUMEN_RMS_UMBRAL = 1.5

audio_actual = None
ultimo_reconocimiento = 0
cooldown_reconocimiento = 4
root = None  # para acceso global en GUI

# Voz
engine = pyttsx3.init()
engine.setProperty("rate", 150)
engine.setProperty("volume", 1.0)

# Normalización del audio 
def normalizar_audio(signal: np.ndarray) -> np.ndarray:
    max_val = np.max(np.abs(signal))
    return signal / max_val if max_val != 0 else signal

# --- Captura ---
def grabar_audio():
    print("🎤 Grabando...")
    audio = sd.rec(int(DURACION * RATE), samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    return audio.flatten()

#Huella digital con FFT y ventana de Hann 
def fingerprint(signal: np.ndarray) -> set:
    if signal.ndim > 1:
        signal = signal[:, 0]
    signal = normalizar_audio(signal)
    hann = np.hanning(len(signal))
    spectrum = np.abs(fft(signal * hann))[:len(signal)//2]
    bins = np.argsort(spectrum)[-CHUNK_TOP:]
    return set(str(b) for b in sorted(bins))

# --- Indexar canciones ---
def indexar_canciones(nuevas_solo=False):
    os.makedirs(HUELLAS_PATH, exist_ok=True)
    for archivo in os.listdir(BASE_PATH):
        if archivo.endswith(".wav"):
            ruta_wav = os.path.join(BASE_PATH, archivo)
            ruta_fp = os.path.join(HUELLAS_PATH, archivo[:-4] + ".fp")
            if nuevas_solo and os.path.exists(ruta_fp):
                continue
            try:
                rate, data = wavfile.read(ruta_wav)
                if data.ndim > 1:
                    data = data[:, 0]
                ventanas = [data[i:i+RATE] for i in range(0, len(data) - RATE, RATE // 2)]
                todo = ["|".join(fingerprint(v)) for v in ventanas if len(v) >= RATE]
                if todo:
                    with open(ruta_fp, "w") as f:
                        f.write("\n".join(todo))
                    print(f"✅ Huella guardada: {ruta_fp}")
                else:
                    print(f"⚠️ No se generaron huellas para: {archivo}")
            except Exception as e:
                print(f"❌ Error procesando {archivo}: {e}")

# --- Indexar huellas tipo hash ---
def indexar_canciones_hash():
    HUELLAS_HASH_PATH = os.path.join(BASE_DIR, "huellas_hash")
    os.makedirs(HUELLAS_HASH_PATH, exist_ok=True)

    for archivo in os.listdir(BASE_PATH):
        if archivo.endswith(".wav"):
            nombre = os.path.splitext(archivo)[0]
            ruta_hash = os.path.join(HUELLAS_HASH_PATH, f"{nombre}.hash")
            if os.path.exists(ruta_hash):
                continue
            ruta_wav = os.path.join(BASE_PATH, archivo)
            try:
                rate, data = wavfile.read(ruta_wav)
                if data.ndim > 1:
                    data = data[:, 0]
                hashes = []
                ventanas = [data[i:i+RATE] for i in range(0, len(data) - RATE, RATE // 2)]
                for ventana in ventanas:
                    v = normalizar_audio(ventana)
                    spectrum = np.abs(fft(v * np.hanning(len(v))))[:len(v)//2]
                    peaks = sorted(np.argsort(spectrum)[-CHUNK_TOP:])
                    for i in range(len(peaks)):
                        for j in range(i + 1, min(i + 5, len(peaks))):
                            delta = j - i
                            hashes.append(f"{peaks[i]},{peaks[j]},{delta}")
                if hashes:
                    with open(ruta_hash, "w") as f:
                        f.write("\n".join(hashes))
                    print(f"✅ Huella hash guardada: {ruta_hash}")
                else:
                    print(f"⚠️ No se generaron huellas hash para: {archivo}")
            except Exception as e:
                print(f"❌ Error generando huella hash para {archivo}: {e}")

# --- Cálculo de coincidencias ---
def score_coincidencia(fingerprints_audio, huellas_cancion):
    score = 0
    for fp_ventana in fingerprints_audio:
        coincidencias = [len(fp_ventana & h) / max(len(h), 1) for h in huellas_cancion]
        if coincidencias:
            mejor_ventana = max(coincidencias)
            if mejor_ventana > 0.15:
                score += mejor_ventana
    return score / len(fingerprints_audio) if fingerprints_audio else 0

# --- Reconocer canción ---
def reconocer(audio):
    rms = np.sqrt(np.mean(audio.astype(np.float32)**2))
    print(f"🔊 RMS detectado: {rms:.2f}")
    if rms < VOLUMEN_RMS_UMBRAL:
        return "Silencio detectado"

    ventanas = [audio[i:i+RATE] for i in range(0, len(audio) - RATE, RATE // 2)]
    fingerprints_audio = [fingerprint(v) for v in ventanas if len(v) >= RATE]

    mejor, mejor_score = "Desconocida", 0

    for archivo in os.listdir(HUELLAS_PATH):
        if archivo.endswith(".fp"):
            with open(os.path.join(HUELLAS_PATH, archivo)) as f:
                huellas_cancion = [set(line.strip().split("|")) for line in f if line.strip()]
            score = score_coincidencia(fingerprints_audio, huellas_cancion)
            print(f"🎯 Coincidencia promedio con {archivo[:-3]}: {score:.2%}")
            if score > mejor_score and score >= UMBRAL_COINCIDENCIAS:
                mejor_score = score
                mejor = archivo[:-3]

    return mejor

# --- Reproducción y voz ---
def hablar(texto):
    try:
        engine.say(texto)
        engine.runAndWait()
    except Exception as e:
        print(f"🗣️ Error hablando: {e}")

reproductor = None

def detener_audio():
    global reproductor
    if reproductor and reproductor.is_alive():
        reproductor.terminate()
        reproductor = None

def proceso_reproduccion(ruta):
    playsound(ruta)

def reproducir(nombre):
    global reproductor
    archivos = os.listdir(FONDOS_PATH)
    candidatos = [f for f in archivos if f.lower().startswith(nombre.lower()) and f.endswith(".mp3")]
    if not candidatos:
        print(f"⚠️ No se encontró archivo para: {nombre}")
        return
    ruta = os.path.join(FONDOS_PATH, candidatos[0])
    print(f"🎵 Reproduciendo: {ruta}")
    detener_audio()
    reproductor = multiprocessing.Process(target=proceso_reproduccion, args=(ruta,), daemon=True)
    reproductor.start()

# --- GUI ---
def gui_reconocer(lbl):
    global ultimo_reconocimiento
    ahora = time.time()
    if ahora - ultimo_reconocimiento < cooldown_reconocimiento:
        lbl.config(text="⏳ Espera un momento antes de reconocer de nuevo", fg="orange")
        return

    ultimo_reconocimiento = ahora
    lbl.config(fg="yellow", text="🎤 Escuchando...")
    indexar_canciones(nuevas_solo=True)
    audio = grabar_audio()
    cancion = reconocer(audio)
    if cancion == "Silencio detectado":
        lbl.config(fg="gray", text="🤫 No se detectó sonido")
        hablar("No se detectó sonido")
    elif cancion == "Desconocida":
        lbl.config(fg="orange", text="🤔 Canción desconocida")
        hablar("Canción desconocida")
    else:
        lbl.config(fg="lightgreen", text=f"🎶 Canción detectada: {cancion}")
        hablar(f"Canción encontrada: {cancion}")
        reproducir(cancion)

def gui_aprender(lbl):
    def continuar_aprendizaje(nombre):
        if not nombre:
            lbl.config(text="⚠️ Canción descartada", fg="red")
            return
        lbl.config(text="🎙️ Grabando nueva canción…", fg="yellow")
        audio = grabar_audio()
        os.makedirs(BASE_PATH, exist_ok=True)
        os.makedirs(HUELLAS_PATH, exist_ok=True)
        lbl.config(text="📀 Guardando canción…", fg="cyan")
        ruta_wav = os.path.join(BASE_PATH, f"{nombre}.wav")
        wavfile.write(ruta_wav, RATE, audio.astype("int16"))
        fp_path = os.path.join(HUELLAS_PATH, f"{nombre}.fp")
        if os.path.exists(fp_path):
            os.remove(fp_path)
        indexar_canciones(nuevas_solo=False)
        if os.path.exists(fp_path):
            lbl.config(text=f"✅ '{nombre}' guardada e indexada", fg="lightgreen")
            messagebox.showinfo("Éxito", f"'{nombre}' se añadió a la base.")
        else:
            lbl.config(text=f"⚠️ '{nombre}' no fue indexada", fg="red")
            messagebox.showwarning("Advertencia", f"La canción '{nombre}' fue guardada pero no se pudo indexar.")
        detener_audio()

    def solicitar_nombre():
        nombre = simpledialog.askstring("Nueva canción", "Nombre:")
        threading.Thread(target=continuar_aprendizaje, args=(nombre,), daemon=True).start()

    root.after(0, solicitar_nombre)


def crear_interfaz():
    indexar_canciones_hash()
    global root
    os.makedirs(BASE_PATH, exist_ok=True)
    os.makedirs(FONDOS_PATH, exist_ok=True)
    os.makedirs(HUELLAS_PATH, exist_ok=True)
    indexar_canciones()

    root = tk.Tk()
    root.title("SASHA")
    root.geometry("1280x720")
    root.configure(bg="black")

    if os.path.exists(FONDO_IMAGEN):
        fondo_img = Image.open(FONDO_IMAGEN)
        fondo_img = fondo_img.resize((1280, 720))
        fondo_img = ImageTk.PhotoImage(fondo_img)
        fondo_label = tk.Label(root, image=fondo_img)
        fondo_label.place(x=0, y=0, relwidth=1, relheight=1)
        fondo_label.image = fondo_img

    estado = tk.Label(root, text="🎵 Esperando…", bg="black", fg="white", font=("Arial", 22))
    estado.pack(pady=20)

    botones = tk.Frame(root, bg="black")
    botones.pack(pady=30)

    estilo_btn = {"width": 18, "height": 2, "font": ("Arial", 14), "relief": "flat", "bd": 0}

    tk.Button(botones, text="🎧 Reconocer", bg="#00FFEE", fg="black", **estilo_btn,
              command=lambda: threading.Thread(target=gui_reconocer, args=(estado,), daemon=True).start()).grid(row=0, column=0, padx=10)

    tk.Button(botones, text="➕ Nueva Canción", bg="#FFA500", fg="black", **estilo_btn,
              command=lambda: gui_aprender(estado)).grid(row=0, column=1, padx=10)

    tk.Button(botones, text="⏹ Detener", width=12, height=2, bg="red", fg="white", font=("Arial", 14), relief="flat",
              command=lambda: (estado.config(text="⏹ Detenido", fg="white"), detener_audio())).grid(row=0, column=2, padx=10)

    tk.Button(root, text="❌ Salir", bg="gray", fg="white", font=("Arial", 14), relief="flat",
              command=lambda: (detener_audio(), root.destroy())).pack(pady=10)

    root.mainloop()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    crear_interfaz()