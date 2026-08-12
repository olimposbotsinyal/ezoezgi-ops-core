import subprocess
import datetime
import sys

def update_requirements():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"requirements.txt güncelleniyor: {today}")

    # pip freeze ile mevcut bağımlılıkları al
    result = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True)
    if result.returncode != 0:
        print("Hata: pip freeze çalıştırılamadı.")
        return

    requirements = result.stdout.strip().split('\n')

    # ta-lib için özel kontrol: Eğer yüklü değilse veya eksikse, manuel ekle
    # (Sen başka yerden yüklediğin için, pip freeze yakalamayabilir)
    talib_found = any('TA-Lib' in req for req in requirements)
    if not talib_found:
        # Manuel olarak ekle (sürümü ayarlayabilirsin)
        requirements.append('TA-Lib==0.4.25')  # Veya git linki: '-e git+https://github.com/mrjbq7/ta-lib.git#egg=TA-Lib'
        print("TA-Lib manuel olarak eklendi (özel yükleme nedeniyle).")

    # Dosyaya yaz
    with open("requirements.txt", "w") as f:
        f.write('\n'.join(requirements) + '\n')

    print("requirements.txt başarıyla güncellendi. TA-Lib kontrol edildi.")

# Haftalık kontrol (Cumartesi günü çalıştır)
if datetime.datetime.now().weekday() == 5:  # 0=Monday, 5=Saturday
    update_requirements()
else:
    print("Bugün Cumartesi değil, güncelleme yapılmadı. Manuel çalıştırmak için: python update_requirements.py")

# Eğer script manuel çalıştırılırsa, güncelleme yap
if __name__ == "__main__":
    update_requirements()
