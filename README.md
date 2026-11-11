# Günlük E-posta Özeti

Gmail gelen kutunuzu her gün otomatik olarak tarar, e-postaları Google Gemini ile
içeriğe göre kategorize eder ve öncelik sırasına göre Türkçe bir özet hazırlayıp
size e-posta olarak gönderir.

## Neler yapıyor?

- **IMAP ile Gmail taraması:** Son 24 saatte (ayarlanabilir) gelen e-postaları okur.
- **Gemini ile analiz:** Her e-postayı özetler, önemli tarih/tutar/aksiyon
  isteklerini çıkarır ve `yüksek / orta / düşük` öncelik atar.
- **Kategorizasyon:** İş, Kişisel, Fatura/Finans, Reklam/Promosyon,
  Bildirim/Otomatik, Spam/Şüpheli ve Diğer.
- **HTML + düz metin rapor:** Kategori kartları, öncelik renkleri ve özet
  tablosu içeren e-posta üretir; rapor `reports/` altına da kaydedilir.
- **SMTP ile gönderim:** Özeti Gmail SMTP üzerinden size (veya `DIGEST_TO`
  adresine) yollar.
- **Otomatik zamanlama:** macOS `launchd` ile her gün 12:00'de çalışır.
- **Dayanıklılık:** IMAP yeniden deneme, Gemini model yedekleme ve hata
  durumunda günlük kaydı.

## Kurulum

1. **Gmail uygulama şifresi oluşturun**
   - Google Hesabı > Güvenlik > 2 Adımlı Doğrulama açık olmalı.
   - https://myaccount.google.com/apppasswords adresinden "Posta" için şifre oluşturun.

2. **Gemini API anahtarı alın**
   - https://aistudio.google.com/apikey

3. **Yapılandırma**
   ```bash
   cp .env.example .env
   # .env dosyasını açıp GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GEMINI_API_KEY doldurun
   ```

4. **Manuel test**
   ```bash
   python3 email_digest.py --dry-run   # e-posta göndermeden raporu ekranda gösterir
   python3 email_digest.py             # gerçekten özet e-postası gönderir
   ```

5. **Günlük zamanlanmış görevi kur (her gün 12:00)**
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

## Kategoriler

İş, Kişisel, Fatura/Finans, Reklam/Promosyon, Bildirim/Otomatik, Spam/Şüpheli, Diğer

## Dosyalar

- `email_digest.py` : Ana uygulama (IMAP -> Gemini -> SMTP)
- `install.sh`      : macOS launchd zamanlanmış görevi kurar
- `.env.example`    : Örnek yapılandırma
- `reports/`        : Üretilen HTML raporları (repoya dahil edilmez)
- `logs/`           : Çalışma kayıtları (repoya dahil edilmez)

## Güvenlik

`.env` dosyası, `logs/` ve `reports/` klasörleri `.gitignore` ile hariç
tutulmuştur; gerçek kimlik bilgileri ve e-posta içerikleri repoya yüklenmez.
