import json
import re
import os


def fix_corrupted_json_file(filepath):
    """
    Bozuk JSON dosyasındaki (NaN, Infinity) değerlerini temizler ve dosyayı onarır.
    """
    full_path = os.path.join(os.getcwd(), filepath)

    if not os.path.exists(full_path):
        print(f"❌ Dosya bulunamadı: {full_path}")
        return

    print(f"🛠️  Dosya okunuyor: {filepath}")

    try:
        # 1. Dosyayı saf metin (string) olarak oku
        with open(full_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        # 2. Regex ile hatalı formatları JSON standardına (null) çevir
        # Python 'NaN' yazar, JSON 'null' ister.
        fixed_content = re.sub(r':\s*NaN\b', ': null', raw_content)
        fixed_content = re.sub(r':\s*Infinity\b', ': null', fixed_content)
        fixed_content = re.sub(r':\s*-Infinity\b', ': null', fixed_content)

        # 3. JSON olarak parse et (Kontrol amaçlı)
        data = json.loads(fixed_content)

        # 4. Temizlenmiş veriyi diske geri kaydet
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        record_count = len(data) if isinstance(data, list) else 0
        print(f"✅ Dosya başarıyla ONARILDI ve KAYDEDİLDİ.")
        print(f"📊 Toplam İşlem Kaydı: {record_count}")

    except json.JSONDecodeError as e:
        print(f"❌ HATA: Dosya temizlenmesine rağmen geçerli JSON formatına dönüşemedi.")
        print(f"Hata detayı: {e}")
    except Exception as e:
        print(f"❌ Beklenmeyen bir hata oluştu: {e}")


if __name__ == "__main__":
    # Dosya yolunu buraya yazın
    target_file = "analytics/closed_signals_state.json"
    fix_corrupted_json_file(target_file)
