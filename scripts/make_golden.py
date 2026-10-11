"""Write tests/golden.json: 10 base questions x 5 languages = 50 questions with the record each should surface."""
import json
from pathlib import Path

BASE = [  # (English, expected record title)
    ("I am a farmer, which scheme gives income support?", "PM-KISAN"),
    ("Scholarship for girls studying engineering", "AICTE Pragati Scholarship for Girls"),
    ("Pension for senior citizens", "Indira Gandhi National Old Age Pension Scheme"),
    ("How to apply for the IAS exam", "UPSC Civil Services Examination"),
    ("Entrance exam for medical college", "NEET (UG)"),
    ("Entrance exam for engineering college", "JEE Main"),
    ("Loan to start a small business", "Pradhan Mantri Mudra Yojana"),
    ("Health insurance for poor families", "Ayushman Bharat PM-JAY"),
    ("Skill training for youth", "Pradhan Mantri Kaushal Vikas Yojana"),
    ("How to register as an unorganised worker", "e-Shram registration"),
]

NATIVE = {
    "hi-IN": ["मैं किसान हूँ, कौन सी योजना आय सहायता देती है?", "इंजीनियरिंग पढ़ने वाली लड़कियों के लिए छात्रवृत्ति",
              "वरिष्ठ नागरिकों के लिए पेंशन", "आईएएस परीक्षा के लिए आवेदन कैसे करें", "मेडिकल कॉलेज में दाखिले की प्रवेश परीक्षा",
              "इंजीनियरिंग कॉलेज की प्रवेश परीक्षा", "छोटा व्यवसाय शुरू करने के लिए ऋण", "गरीब परिवारों के लिए स्वास्थ्य बीमा",
              "युवाओं के लिए कौशल प्रशिक्षण", "असंगठित मजदूर के रूप में पंजीकरण कैसे करें"],
    "bn-IN": ["আমি একজন কৃষক, কোন প্রকল্প আয় সহায়তা দেয়?", "ইঞ্জিনিয়ারিং পড়ুয়া মেয়েদের জন্য বৃত্তি", "বয়স্ক নাগরিকদের জন্য পেনশন",
              "আইএএস পরীক্ষার জন্য কীভাবে আবেদন করব", "মেডিকেল কলেজে ভর্তির প্রবেশিকা পরীক্ষা", "ইঞ্জিনিয়ারিং কলেজের প্রবেশিকা পরীক্ষা",
              "ছোট ব্যবসা শুরু করার জন্য ঋণ", "গরিব পরিবারের জন্য স্বাস্থ্য বিমা", "তরুণদের জন্য দক্ষতা প্রশিক্ষণ",
              "অসংগঠিত শ্রমিক হিসেবে নিবন্ধন কীভাবে করব"],
    "ta-IN": ["நான் ஒரு விவசாயி, எந்த திட்டம் வருமான உதவி வழங்குகிறது?", "பொறியியல் படிக்கும் பெண்களுக்கான உதவித்தொகை",
              "மூத்த குடிமக்களுக்கான ஓய்வூதியம்", "ஐஏஎஸ் தேர்வுக்கு எப்படி விண்ணப்பிப்பது", "மருத்துவக் கல்லூரி சேர்க்கைக்கான நுழைவுத் தேர்வு",
              "பொறியியல் கல்லூரி நுழைவுத் தேர்வு", "சிறு தொழில் தொடங்க கடன்", "ஏழைக் குடும்பங்களுக்கான மருத்துவக் காப்பீடு",
              "இளைஞர்களுக்கான திறன் பயிற்சி", "அமைப்புசாரா தொழிலாளராக பதிவு செய்வது எப்படி"],
    "te-IN": ["నేను రైతును, ఏ పథకం ఆదాయ సహాయం ఇస్తుంది?", "ఇంజనీరింగ్ చదివే అమ్మాయిలకు స్కాలర్‌షిప్", "వృద్ధులకు పింఛన్",
              "ఐఏఎస్ పరీక్షకు ఎలా దరఖాస్తు చేయాలి", "మెడికల్ కాలేజీ ప్రవేశ పరీక్ష", "ఇంజనీరింగ్ కాలేజీ ప్రవేశ పరీక్ష",
              "చిన్న వ్యాపారం ప్రారంభించడానికి రుణం", "పేద కుటుంబాలకు ఆరోగ్య బీమా", "యువతకు నైపుణ్య శిక్షణ",
              "అసంఘటిత కార్మికుడిగా నమోదు చేసుకోవడం ఎలా"],
    "mr-IN": ["मी शेतकरी आहे, कोणती योजना उत्पन्न मदत देते?", "इंजिनिअरिंग शिकणाऱ्या मुलींसाठी शिष्यवृत्ती", "ज्येष्ठ नागरिकांसाठी पेन्शन",
              "आयएएस परीक्षेसाठी अर्ज कसा करावा", "वैद्यकीय महाविद्यालय प्रवेश परीक्षा", "अभियांत्रिकी महाविद्यालय प्रवेश परीक्षा",
              "लहान व्यवसाय सुरू करण्यासाठी कर्ज", "गरीब कुटुंबांसाठी आरोग्य विमा", "तरुणांसाठी कौशल्य प्रशिक्षण",
              "असंघटित कामगार म्हणून नोंदणी कशी करावी"],
}

items = []
for lang, qs in NATIVE.items():
    assert len(qs) == len(BASE)
    for (en, title), q in zip(BASE, qs):
        items.append({"lang": lang, "q": q, "en": en, "expect": title})

path = Path(__file__).resolve().parent.parent / "tests" / "golden.json"
path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(items), "golden questions written")
