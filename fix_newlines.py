import os
import glob

def fix_newlines():
    # Ana dosyalar ve core/scripts altındaki tüm Python dosyaları
    files_to_fix = [
        "scalp_bot_v3.py", "app.py", "execution_engine.py", 
        "database.py", "dashboard_service.py", "telegram_delivery.py", "config.py"
    ]
    
    # core ve scripts klasörlerindeki .py dosyalarını da ekle
    files_to_fix.extend(glob.glob("core/*.py"))
    files_to_fix.extend(glob.glob("scripts/*.py"))

    fixed_count = 0
    for filepath in files_to_fix:
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                content = f.read()
            
            # Eğer dosyada \r var ama \n yoksa (veya azsa), satır sonları bozulmuştur
            if b'\r' in content:
                # Universal newline dönüşümü: tüm \r\n ve \r'leri \n yap
                new_content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                
                if new_content != content:
                    with open(filepath, 'wb') as f:
                        f.write(new_content)
                    print(f"DUZELTILDI: {filepath}")
                    fixed_count += 1
                else:
                    print(f"GEREK YOK: {filepath}")
        else:
            print(f"BULUNAMADI: {filepath}")
            
    print(f"\nToplam {fixed_count} dosyanın satır sonları (CR -> LF) düzeltildi.")

if __name__ == "__main__":
    fix_newlines()
