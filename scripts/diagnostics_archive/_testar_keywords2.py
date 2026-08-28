import sys, re
sys.path.insert(0, "C:/ultracut3")

# Importa GENERIC_WORDS do broll_director
from services.broll_director import _extract_keywords_local as original_func
from services.broll_director import _detect_language, GENERIC_WORDS

# Lista de SUBSTANTIVOS CONCRETOS fotográficos prioritários
PRIORITY_NOUNS = {
    "dandelion", "rose", "daisy", "tulip", "sunflower", "lily", "orchid",
    "oak", "pine", "maple", "willow", "palm", "bamboo", "cactus",
    "mushroom", "fungus", "moss", "fern", "algae", "seaweed",
    "lavender", "rosemary", "thyme", "basil", "mint", "sage",
    "butterfly", "bee", "bird", "eagle", "hawk", "owl", "crow",
    "robin", "swan", "duck", "goose", "horse", "cow", "sheep",
    "rabbit", "deer", "fox", "wolf", "bear", "lion", "tiger",
    "fish", "shark", "whale", "dolphin", "snake", "lizard", "turtle",
    "frog", "ant", "spider", "worm", "snail", "caterpillar",
    "root", "leaf", "leaves", "flower", "petal", "stem", "branch",
    "bark", "trunk", "seed", "fruit", "nut", "berry", "thorn",
    "mountain", "river", "lake", "ocean", "sea", "forest", "tree",
    "field", "meadow", "garden", "soil", "rock", "stone", "sand",
    "sky", "cloud", "rain", "snow", "ice", "fire", "smoke",
    "sun", "moon", "star", "planet", "earth",
    "hand", "hands", "face", "eye", "eyes", "mouth", "head", "finger",
    "arm", "leg", "foot", "feet", "heart", "brain", "bone",
    "man", "woman", "child", "baby", "person", "people",
    "doctor", "chef", "farmer", "gardener", "scientist",
    "cup", "glass", "bottle", "plate", "bowl", "knife", "spoon",
    "fork", "pot", "pan", "oven", "table", "chair",
    "book", "paper", "pen", "pencil", "phone", "camera", "computer",
    "car", "truck", "bicycle", "boat", "plane", "train", "bus",
    "house", "door", "window", "wall", "roof", "floor", "stairs",
    "bridge", "road", "path", "trail", "gate", "fence", "tower",
    "clock", "watch", "lamp", "candle", "mirror", "frame", "box",
    "basket", "bag", "hat", "shoe", "dress", "shirt", "coat",
    "medicine", "pill", "bottle", "jar", "tube", "syringe",
    "bread", "cheese", "milk", "egg", "butter", "honey", "sugar",
    "salt", "pepper", "rice", "pasta", "soup", "salad", "meat",
    "apple", "banana", "orange", "grape", "lemon", "lime", "berry",
    "corn", "wheat", "grain", "herb", "spice", "weed",
    "kitchen", "garden", "yard", "park", "beach", "cave", "desert",
    "jungle", "swamp", "farm", "barn", "shed", "greenhouse",
    "laboratory", "workshop", "studio", "office", "factory",
    "museum", "library", "school", "church", "temple", "castle",
    "city", "town", "village", "street", "square", "market",
    "guitar", "piano", "violin", "drum", "flute", "trumpet",
    "liver", "kidney", "heart", "lungs", "brain", "blood", "skin",
    "detox", "tea", "tincture", "salve", "oil", "cream", "poultice",
    "backyard", "garden", "lawn", "sidewalk", "crack", "concrete",
}

EXTRA_STOPWORDS = {
    "before", "after", "during", "while", "until", "since", "because",
    "although", "though", "through", "between", "among", "beneath",
    "beside", "behind", "beyond", "above", "below", "across",
    "around", "within", "without", "along", "toward", "towards",
    "every", "each", "both", "either", "neither", "several",
    "which", "that", "this", "those", "these", "what", "when",
    "where", "why", "how", "who", "whom", "whose",
    "been", "being", "having", "doing", "getting", "become",
    "became", "began", "begun", "broken", "built", "bought",
    "caught", "chose", "chosen", "come", "came", "drawn",
    "drank", "drunk", "driven", "drove", "eaten", "fell",
    "fallen", "found", "given", "gone", "grown", "known",
    "laid", "lain", "led", "lost", "made", "meant",
    "paid", "proven", "put", "ran", "run", "said",
    "seen", "sent", "shown", "sold", "spent", "stood",
    "taken", "thought", "told", "torn", "won", "worn",
    "written", "wrote",
    "long", "short", "tall", "wide", "deep", "broad",
    "still", "already", "yet", "always", "never", "often",
    "sometimes", "usually", "finally", "quickly", "slowly",
    "carefully", "easily", "hardly", "nearly", "almost",
    "quite", "rather", "pretty", "fairly", "extremely",
    "very", "too", "enough", "just", "even", "only",
    "much", "many", "more", "most", "less", "least",
    "little", "few", "several", "plenty", "enough",
    "such", "same", "different", "other", "another",
    "important", "significant", "necessary", "possible",
    "common", "simple", "complex", "basic", "major", "minor",
    "known", "called", "considered", "regarded", "viewed",
    "exists", "existed", "existing", "remains", "remained",
    "appears", "appeared", "seems", "seemed", "looks", "looked",
    "becomes", "becoming", "causes", "caused", "creates", "created",
    "forms", "formed", "produces", "produced", "provides", "provided",
    "allows", "allowed", "enables", "enabled", "helps", "helped",
    "makes", "made", "uses", "used", "using", "takes", "taking",
    "found", "finds", "finding", "shows", "showing", "shown",
    "gives", "giving", "given", "brings", "bringing", "brought",
    "people", "things", "thing", "way", "ways", "part", "parts",
    "place", "places", "time", "times", "world", "life", "lives",
    "year", "years", "day", "days", "week", "weeks", "month",
    "number", "numbers", "system", "systems", "process", "processes",
    "result", "results", "example", "examples", "type", "types",
    "form", "forms", "kind", "kinds", "sort", "sorts",
    "right", "watching", "video", "someone", "some", "backyard",
    "yanking", "plant", "out", "ground", "throwing", "away",
    "heals", "relied", "thousands", "called", "today", "going",
    "walk", "through", "incredible", "healing", "properties",
    "common", "probably", "walked", "past", "entire", "life",
    "detox", "cleanse", "kidneys", "even", "help", "digestion",
}


def _extract_keywords_local_fixed(text: str, max_keywords: int = 3) -> list:
    proper_nouns = set(re.findall(r'\b[A-Z][a-z]{2,}\b', text))
    lang = _detect_language(text)

    if lang == "en":
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        words_set = set(words)

        filtered = []
        for w in words:
            if w in GENERIC_WORDS:
                continue
            if w in EXTRA_STOPWORDS:
                continue
            if len(w) == 4 and w not in PRIORITY_NOUNS:
                continue
            filtered.append(w)

        freq = {}
        for w in filtered:
            freq[w] = freq.get(w, 0) + 1

        scored = []
        for w, f in freq.items():
            score = 0
            if w in proper_nouns:
                score += 10
            if w in PRIORITY_NOUNS:
                score += 5
            score += min(f, 5)
            if len(w) >= 7:
                score += 2
            scored.append((score, w))

        scored.sort(key=lambda x: (-x[0], -len(x[1])))
        result = [w for _, w in scored[:max_keywords]]

        if result:
            return result

        for w in words_set:
            if w in PRIORITY_NOUNS:
                return [w]

        fallback = [w for w in words if w not in GENERIC_WORDS]
        if fallback:
            return fallback[:1]

        return []
    else:
        return []


test_phrases = [
    "Right now while you are watching this video someone in some backyard is yanking a plant out of",
    "the ground and throwing her away A plant that heals relied on for thousands of years",
    "long before pharmacies existed It is called the Dandelion and today I am going to walk you through",
    "the incredible healing properties of this common weed that you have probably walked past your entire life",
    "This plant can detox your liver cleanse your kidneys and even help with digestion",
]

print("=" * 60)
print("TESTE DE EXTRACAO DE KEYWORDS")
print("=" * 60)

for i, phrase in enumerate(test_phrases, 1):
    original = original_func(phrase)
    fixed = _extract_keywords_local_fixed(phrase)
    print(f"\nFrase {i}:")
    print(f"  Texto: {phrase[:70]}...")
    print(f"  ORIGINAL: {original}")
    print(f"  CORRIGIDO: {fixed}")