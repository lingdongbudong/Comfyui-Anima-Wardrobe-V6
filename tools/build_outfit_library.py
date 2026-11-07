# -*- coding: utf-8 -*-
"""全局服装库：**校验 + 派生档位**，产出 presets/outfit_library.json。

为什么要有这个脚本（与 build_scenes.py 同一套纪律）：
  1. `level` 必须由内容算（= max(各 fields 的 option_level, 各 tags 的 tag_level)），
     手写必然漂移 —— 而它决定"这套衣服在哪些档位可用"，写错就是闸门漏；
  2. `fields` 的键/值必须**真实存在于词库**，写错一个词就是静默失效
     （引擎侧只认词库里的原文，不匹配就丢掉，不报错）；
  3. `drop` 缺省 = 13 个服装字段全集（与角色服装预设同口径）。

用法：
    python tools/build_outfit_library.py            # 校验 + 回写（幂等）
    python tools/build_outfit_library.py --check    # 只校验，不写（测试用）

⚠️ `_meta.active`（启用中的那一套）、`_meta.overrides`（袜子/鞋子覆盖项）、
   `_meta.scene_override`（是否覆盖场景推荐服装）都属于**用户状态**，
   重跑本脚本必须保留 —— 否则每次构建都会把用户的选择清掉。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

# 与 build_scenes.py 一致：用 tests/_boot.py 造空包再 import，
# 避免包内相对导入炸掉。
sys.path.insert(0, os.path.join(PLUGIN, "tests"))
import _boot  # noqa: E402

_R = _boot.load("anima_v4_rules")
field_by_key = _R.field_by_key
option_level = _R.option_level
tag_level = _R.tag_level

LIB = os.path.join(PLUGIN, "presets", "outfit_library.json")

SCHEMA = "anima-v5/outfit-library@1"

# 13 个服装字段（与 rules.OUTFIT_FIELDS 同口径）。预设默认全部闭麦。
DROP_DEFAULT = list(_R.OUTFIT_FIELDS)

CATEGORIES = [
    {"id": "casual", "zh": "日常休闲", "desc": "T恤 / 卫衣 / 牛仔 / 针织 —— 最通用的日常底子"},
    {"id": "school", "zh": "校园制服", "desc": "水手服 / 西装校服 / 体操服 / 啦啦队"},
    {"id": "office", "zh": "职业通勤", "desc": "西装 / 衬衫 / 空乘 / 警察 / 白大褂"},
    {"id": "traditional", "zh": "传统服饰", "desc": "和服 / 浴衣 / 汉服 / 旗袍 / 巫女 / 纱丽"},
    {"id": "formal", "zh": "礼服派对", "desc": "晚礼服 / 婚纱 / 洛丽塔 / 偶像舞台 / 魔法少女"},
    {"id": "home", "zh": "居家睡衣", "desc": "睡衣 / 睡袍 / 浴袍 / 男友衬衫 / 家居服"},
    {"id": "swim", "zh": "泳装内衣", "desc": "比基尼 / 连体泳衣 / 竞泳 / 蕾丝内衣（L1–L3）"},
    {"id": "combat", "zh": "战斗机能", "desc": "赛博 / 战术 / 忍者 / 骑士 / 紧身衣"},
    {"id": "cosplay", "zh": "主题扮演", "desc": "女仆 / 兔女郎 / 护士 / 小恶魔 / 圣诞"},
    # v5.2.0（用户 2026-11-05 18:52）：「丰富服装款式，例如各种搭配丝星点袜丝袜的服装」
    {"id": "hosiery", "zh": "丝袜穿搭",
     "desc": "以亮丝星点袜（黑/白/淡粉/薰衣草/酒红 × 连裤/过膝/吊带）为核心的整套搭配"},
]

# ⚠️ 写条目时的硬约束：
#    · tags >= 3 条（实测 <3 条必翻车，见 README v5.0.2 段）；
#    · tags 一律小写、不带逗号；不进词库的细节全部放这里；
#    · fields 只用词库真实选项（脚注：`ballet slippers` / `plugsuit` 不在词库，
#      所以那两套只写 tags，fields 留空或只写颜色）；
#    · 不写 `count` / `role`（地基字段）。
OUTFITS = [
    # ── 日常休闲 ────────────────────────────────────────────────────
    dict(id="ol_knit_pleated", zh="露肩针织 · 百褶裙", cat="casual",
         note="日常通勤向，冷清干净的白天感",
         tags=["white off-shoulder sweater", "black pleated skirt",
               "brown loafers", "knitwear", "crossed legs"],
         fields={"outfit": "sweater and skirt", "top": "off-shoulder sweater",
                 "bottom": "pleated skirt", "shoes": "loafers",
                 "cloth_color": "white", "cloth_mat": "knitwear",
                 "cloth_state_sfw": "off shoulder"}),
    dict(id="ol_hoodie_shorts", zh="灰卫衣 · 短裤", cat="casual",
         note="周末居家向，松垮衣服的慵懒感",
         tags=["grey oversized hoodie", "black shorts", "white sneakers",
               "white ankle socks", "oversized clothes"],
         fields={"outfit": "hoodie and shorts", "top": "hoodie",
                 "bottom": "shorts", "socks": "ankle socks",
                 "shoes": "sneakers", "cloth_color": "grey",
                 "cloth_state_sfw": "oversized clothes"}),
    dict(id="ol_denim_tee", zh="白T · 牛仔裙", cat="casual",
         note="夏日干净利落，不出手套袜",
         tags=["white t-shirt", "blue denim skirt", "white sneakers",
               "bare legs", "tucked in"],
         fields={"top": "t-shirt", "bottom": "denim skirt", "shoes": "sneakers",
                 "cloth_color": "white", "cloth_mat": "denim",
                 "cloth_state_sfw": "tucked in"}),
    dict(id="ol_cardigan_pencil", zh="米色开衫 · 铅笔裙", cat="casual",
         note="轻熟通勤，有腰线",
         tags=["beige long cardigan", "black pencil skirt", "white blouse",
               "black pantyhose", "chunky heels", "office lady"],
         fields={"top": "cardigan", "bottom": "pencil skirt",
                 "socks": "black pantyhose", "shoes": "chunky heels",
                 "cloth_color": "black", "cloth_mat": "knitwear"}),
    dict(id="ol_oversized_tee", zh="宽大T恤 · 赤足", cat="casual",
         note="最松的日常，只有一件宽大 T",
         tags=["oversized white t-shirt", "bare legs", "barefoot",
               "loungewear", "messy hair"],
         fields={"top": "t-shirt", "socks": "barefoot", "shoes": "bare feet",
                 "cloth_color": "white",
                 "cloth_state_sfw": "oversized clothes"}),
    dict(id="ol_black_slipdress", zh="黑色吊带连衣裙", cat="casual",
         note="一条裙子走天下",
         tags=["black dress", "thin necklace", "black high heels",
               "sleeveless", "bare arms"],
         fields={"outfit": "dress", "shoes": "high heels",
                 "cloth_color": "black"}),
    dict(id="ol_bomber_jeans", zh="飞行夹克 · 牛仔", cat="casual",
         note="街头向，外套敞开",
         tags=["olive bomber jacket", "blue jeans", "black tank top",
               "white sneakers", "zipper"],
         fields={"top": "bomber jacket", "bottom": "jeans", "shoes": "sneakers",
                 "cloth_color": "blue"}),
    dict(id="ol_trench_boots", zh="风衣 · 长靴", cat="casual",
         note="秋冬通勤，外套披肩",
         tags=["beige trench coat", "black turtleneck", "black boots",
               "black leggings", "coat on shoulders"],
         fields={"top": "trench coat", "bottom": "leggings", "shoes": "boots",
                 "cloth_color": "brown",
                 "cloth_state_sfw": "coat on shoulders"}),
    dict(id="ol_crop_gym", zh="短上衣 · 运动长裤", cat="casual",
         note="健身通勤两用，露腰",
         tags=["black crop top", "grey track pants", "white sneakers",
               "midriff peek", "sporty"],
         fields={"top": "crop top", "bottom": "track pants",
                 "shoes": "sneakers", "cloth_state_sfw": "midriff peek"}),
    dict(id="ol_sundress_straw", zh="吊带连衣裙 · 草帽", cat="casual",
         note="夏日出行，草帽点睛",
         tags=["white sundress", "straw hat", "sandals", "bare legs",
               "summer", "sleeveless dress"],
         fields={"outfit": "sundress", "shoes": "sandals",
                 "accessory": "straw hat", "cloth_color": "white"}),

    # ── 校园制服 ────────────────────────────────────────────────────
    dict(id="sc_serafuku_summer", zh="夏水手服", cat="school",
         note="短袖水手 + 蓝百褶 + 白及膝袜",
         tags=["white short-sleeve sailor shirt", "blue pleated skirt",
               "white kneehighs", "brown loafers", "serafuku"],
         fields={"outfit": "serafuku", "top": "sailor shirt",
                 "bottom": "pleated skirt", "socks": "kneehighs",
                 "shoes": "loafers", "cloth_color": "blue"}),
    dict(id="sc_serafuku_winter", zh="冬水手服", cat="school",
         note="长袖藏青 + 红领巾 + 黑及膝袜",
         tags=["navy sailor shirt", "red neckerchief", "navy pleated skirt",
               "black kneehighs", "brown loafers"],
         fields={"outfit": "serafuku", "top": "sailor shirt",
                 "bottom": "pleated skirt", "socks": "kneehighs",
                 "shoes": "loafers", "cloth_color": "blue"}),
    dict(id="sc_gym_uniform", zh="体操服 · 蓝白", cat="school",
         note="体育课标准装",
         tags=["blue gym uniform", "white bloomers", "white sneakers",
               "white socks", "sporty"],
         fields={"outfit": "gym uniform", "bottom": "bloomers",
                 "socks": "white socks", "shoes": "sneakers",
                 "cloth_color": "blue"}),
    dict(id="sc_school_swimsuit", zh="旧式学校泳装", cat="school",
         note="蓝底白名札，湿发",
         tags=["school swimsuit", "blue one-piece swimsuit", "white name tag",
               "barefoot", "wet hair"],
         fields={"outfit": "school swimsuit", "socks": "barefoot",
                 "shoes": "bare feet", "cloth_color": "blue"}),
    dict(id="sc_blazer_plaid", zh="西装校服 · 格裙", cat="school",
         note="灰西装 + 红格裙",
         tags=["school uniform", "grey blazer", "red plaid skirt",
               "white shirt", "black loafers"],
         fields={"outfit": "school uniform", "top": "blazer",
                 "bottom": "pleated skirt", "shoes": "loafers",
                 "cloth_color": "grey"}),
    dict(id="sc_tracksuit", zh="运动套装", cat="school",
         note="整套运动服，拉链拉到头",
         tags=["track suit", "red track jacket", "black track pants",
               "white sneakers", "zipper"],
         fields={"outfit": "track suit", "top": "jacket",
                 "bottom": "track pants", "shoes": "sneakers",
                 "cloth_color": "red"}),
    dict(id="sc_cardigan_uniform", zh="开衫校服", cat="school",
         note="米开衫 + 白衬衫 + 藏青裙",
         tags=["beige cardigan", "white shirt", "navy pleated skirt",
               "brown loafers", "school uniform"],
         fields={"outfit": "school uniform", "top": "cardigan",
                 "bottom": "pleated skirt", "shoes": "loafers",
                 "cloth_color": "brown"}),
    dict(id="sc_sailor_dress", zh="水手连衣裙", cat="school",
         note="一整件水手裙，白领蓝身",
         tags=["sailor dress", "white sailor collar", "blue dress",
               "mary janes", "white socks"],
         fields={"outfit": "sailor dress", "shoes": "mary janes",
                 "socks": "white socks", "cloth_color": "blue"}),
    dict(id="sc_cheerleader", zh="啦啦队", cat="school",
         note="红百褶 + 白短上衣 + 彩球",
         tags=["cheerleader uniform", "red pleated skirt", "white crop top",
               "white sneakers", "pom pom", "midriff peek"],
         fields={"outfit": "cheerleader uniform", "top": "crop top",
                 "bottom": "pleated skirt", "shoes": "sneakers",
                 "cloth_color": "red"}),
    dict(id="sc_tennis_skirt", zh="网球裙", cat="school",
         note="白 Polo + 白百褶 + 球拍",
         tags=["tennis skirt", "white polo shirt", "white sneakers",
               "white ankle socks", "holding racket", "sporty"],
         fields={"top": "polo shirt", "bottom": "tennis skirt",
                 "shoes": "sneakers", "socks": "ankle socks",
                 "cloth_color": "white"}),

    # ── 职业通勤 ────────────────────────────────────────────────────
    dict(id="of_suit_pencil", zh="灰西装 · 铅笔裙", cat="office",
         note="标准职场，尖头高跟",
         tags=["grey suit", "white blouse", "black pencil skirt",
               "black pantyhose", "pointed toe high heels"],
         fields={"outfit": "suit", "top": "blazer", "bottom": "pencil skirt",
                 "socks": "black pantyhose", "shoes": "pointed toe high heels",
                 "cloth_color": "grey"}),
    dict(id="of_ol_blouse", zh="白衬衫 · 黑裙", cat="office",
         note="最不出错的 OL 组合",
         tags=["office lady", "white blouse", "black pencil skirt",
               "black pantyhose", "chunky heels"],
         fields={"outfit": "office lady", "top": "blouse",
                 "bottom": "pencil skirt", "socks": "black pantyhose",
                 "shoes": "chunky heels", "cloth_color": "black"}),
    dict(id="of_turtleneck_slacks", zh="高领 · 阔腿裤", cat="office",
         note="极简黑白，腰线靠皮带",
         tags=["black turtleneck", "beige wide-leg pants", "black leather belt",
               "black high heels", "tucked in"],
         fields={"top": "turtleneck", "bottom": "wide-leg pants",
                 "shoes": "high heels", "cloth_color": "black",
                 "cloth_state_sfw": "tucked in"}),
    dict(id="of_flight_attendant", zh="空乘制服", cat="office",
         note="藏青套装 + 领巾",
         tags=["flight attendant uniform", "navy blue jacket", "white blouse",
               "blue pencil skirt", "neckerchief"],
         fields={"outfit": "flight attendant uniform", "top": "jacket",
                 "bottom": "pencil skirt", "cloth_color": "blue"}),
    dict(id="of_police", zh="警察制服", cat="office",
         note="藏青衬衫 + 黑靴 + 徽章",
         tags=["police uniform", "navy blue shirt", "black pencil skirt",
               "black boots", "badge"],
         fields={"outfit": "police uniform", "shoes": "boots",
                 "cloth_color": "blue"}),
    dict(id="of_teacher", zh="教师套装", cat="office",
         note="棕开衫 + 长裙 + 眼镜",
         tags=["teacher", "brown cardigan", "white shirt", "long skirt",
               "brown loafers", "glasses"],
         fields={"outfit": "teacher", "top": "cardigan", "bottom": "long skirt",
                 "shoes": "loafers", "accessory": "glasses",
                 "cloth_color": "brown"}),
    dict(id="of_waitress", zh="服务生围裙", cat="office",
         note="黑裙白围裙",
         tags=["waitress", "white apron", "black dress", "white blouse",
               "mary janes"],
         fields={"outfit": "waitress", "shoes": "mary janes",
                 "cloth_color": "black"}),
    dict(id="of_bartender", zh="酒保马甲", cat="office",
         note="黑马甲 + 白衬衫 + 领结",
         tags=["bartender", "black vest", "white shirt", "black trousers",
               "bow tie"],
         fields={"outfit": "bartender", "top": "vest", "bottom": "suit pants",
                 "cloth_color": "black"}),
    dict(id="of_military", zh="军装制服", cat="office",
         note="橄榄绿军装 + 肩章",
         tags=["military uniform", "olive green jacket", "black skirt",
               "black boots", "epaulettes"],
         fields={"outfit": "military uniform", "top": "military jacket",
                 "bottom": "skirt", "shoes": "boots", "cloth_color": "green"}),
    dict(id="of_labcoat", zh="白大褂", cat="office",
         note="医生 / 研究员，听诊器",
         tags=["white coat", "lab coat", "blue shirt", "black pencil skirt",
               "stethoscope"],
         fields={"top": "coat", "bottom": "pencil skirt",
                 "cloth_color": "white"}),

    # ── 传统服饰 ────────────────────────────────────────────────────
    dict(id="tr_kimono_floral", zh="和服 · 花柄", cat="traditional",
         note="粉底花柄 + 腰带 + 木屐",
         tags=["kimono", "floral print kimono", "obi", "geta", "tabi",
               "hair ornament"],
         fields={"outfit": "kimono", "shoes": "geta", "socks": "tabi",
                 "cloth_color": "pink"}),
    dict(id="tr_yukata_navy", zh="浴衣 · 夏祭", cat="traditional",
         note="藏青浴衣 + 团扇",
         tags=["yukata", "blue yukata", "obi", "geta", "uchiwa",
               "summer festival"],
         fields={"outfit": "yukata", "shoes": "geta", "cloth_color": "blue"}),
    dict(id="tr_hanfu_chest", zh="汉服 · 齐胸", cat="traditional",
         note="广袖 + 发饰，飘带",
         tags=["hanfu", "hanfu dress", "wide sleeves", "hair ornament",
               "ribbon", "embroidered"],
         fields={"outfit": "hanfu", "cloth_color": "white"}),
    dict(id="tr_qipao_black", zh="旗袍 · 黑金", cat="traditional",
         note="黑底金线，开衩",
         tags=["china dress", "black qipao", "gold embroidery", "side slit",
               "black high heels"],
         fields={"outfit": "china dress", "shoes": "high heels",
                 "cloth_color": "black"}),
    dict(id="tr_miko", zh="巫女装", cat="traditional",
         note="白衣红袴，最经典的神社配色",
         tags=["miko", "white haori", "red hakama", "tabi", "geta"],
         fields={"outfit": "miko", "socks": "tabi",
                 "shoes": "geta", "cloth_color": "white"}),
    dict(id="tr_hakama_kendo", zh="袴 · 剑道", cat="traditional",
         note="白袴 + 黑衣，竹刀",
         tags=["hakama", "white hakama", "black top", "tabi",
               "holding shinai", "japanese clothes"],
         fields={"outfit": "hakama", "socks": "tabi"}),
    dict(id="tr_xiuhe", zh="秀禾服 · 红金", cat="traditional",
         note="婚服向，凤冠",
         tags=["red hanfu", "gold embroidery", "phoenix crown", "wedding",
               "wide sleeves"],
         fields={"outfit": "hanfu", "cloth_color": "red"}),
    dict(id="tr_arabian", zh="阿拉伯舞娘", cat="traditional",
         note="金饰 + 面纱 + 灯笼裤",
         tags=["arabian dress", "gold jewelry", "bare midriff", "veil",
               "harem pants", "dancer"],
         fields={"bottom": "harem pants", "cloth_color": "yellow"}),
    dict(id="tr_hanbok", zh="韩服", cat="traditional",
         note="粉韩服 + 发带",
         tags=["hanbok", "chima jeogori", "pastel pink", "hair ribbon",
               "long skirt"],
         fields={"bottom": "long skirt", "cloth_color": "pink"}),
    dict(id="tr_sari", zh="印度纱丽", cat="traditional",
         note="红金纱丽 + 额饰",
         tags=["sari", "gold trim", "bindi", "bare midriff", "jewelry"],
         fields={"cloth_color": "red"}),

    # ── 礼服派对 ────────────────────────────────────────────────────
    dict(id="fm_evening_gown", zh="晚礼服 · 黑", cat="formal",
         note="露背 + 长手套，最正式的场合",
         tags=["evening gown", "black evening gown", "backless",
               "black high heels", "elbow gloves"],
         fields={"outfit": "evening gown", "shoes": "high heels",
                 "cloth_color": "black", "cloth_state_sfw": "backless"}),
    dict(id="fm_wedding_dress", zh="婚纱", cat="formal",
         note="蕾丝 + 头纱",
         tags=["wedding dress", "white wedding dress", "veil", "lace",
               "bridal"],
         fields={"outfit": "wedding dress", "cloth_color": "white",
                 "cloth_mat": "lace"}),
    dict(id="fm_cocktail_red", zh="红色小礼服", cat="formal",
         note="派对向，手包必备",
         tags=["cocktail dress", "red dress", "black high heels",
               "clutch bag", "sleeveless"],
         fields={"outfit": "dress", "shoes": "high heels",
                 "cloth_color": "red"}),
    dict(id="fm_gothic_lolita", zh="哥特洛丽塔", cat="formal",
         note="黑裙 + 衬裙 + 蕾丝",
         tags=["gothic lolita", "black frilled dress", "petticoat",
               "mary janes", "lace"],
         fields={"outfit": "gothic lolita", "shoes": "mary janes",
                 "cloth_color": "black", "cloth_mat": "frills"}),
    dict(id="fm_idol_stage", zh="偶像舞台", cat="formal",
         note="荷叶裙 + 白手套 + 长靴",
         tags=["idol costume", "frilled skirt", "white gloves", "boots",
               "stage lighting"],
         fields={"outfit": "idol costume", "bottom": "frilled skirt",
                 "shoes": "boots"}),
    dict(id="fm_ballerina", zh="芭蕾舞裙", cat="formal",
         note="tutu + 芭蕾鞋（鞋不在词库，走 tags）",
         tags=["ballerina", "ballet slippers", "tutu", "leotard",
               "white tights"],
         fields={"outfit": "ballerina", "cloth_color": "white"}),
    dict(id="fm_magical_girl", zh="魔法少女", cat="formal",
         note="荷叶裙 + 魔杖 + 白过膝",
         tags=["magical girl", "frilled dress", "wand", "white thighhighs",
               "ribbons"],
         fields={"outfit": "magical girl", "socks": "white thighhighs",
                 "cloth_color": "pink"}),
    dict(id="fm_jirai_kei", zh="地雷系", cat="formal",
         note="黑裙 + 厚底鞋 + 缎带",
         tags=["jirai kei", "black dress", "white blouse",
               "platform high heels", "ribbon"],
         fields={"outfit": "jirai kei", "shoes": "platform high heels",
                 "cloth_color": "black"}),
    dict(id="fm_ryousangata", zh="量产型", cat="formal",
         note="粉裙 + 白衬衫 + 荷叶边",
         tags=["ryousangata", "pink dress", "white blouse", "ribbon",
               "frilled skirt"],
         fields={"outfit": "ryousangata", "cloth_color": "pink"}),
    dict(id="fm_tuxedo", zh="女式礼服西装", cat="formal",
         note="燕尾 + 领结，中性帅气",
         tags=["tuxedo", "black suit", "white shirt", "bow tie", "suit pants"],
         fields={"outfit": "tuxedo", "bottom": "suit pants",
                 "cloth_color": "black"}),

    # ── 居家睡衣 ────────────────────────────────────────────────────
    dict(id="hm_pajama_pink", zh="粉睡衣", cat="home",
         note="抱着枕头，赤足",
         tags=["pajamas", "pink pajamas", "barefoot", "holding pillow",
               "sleepwear"],
         fields={"cloth_color": "pink", "socks": "barefoot",
                 "shoes": "bare feet"}),
    dict(id="hm_sleeping_kimono", zh="睡袍和服", cat="home",
         note="宽松睡袍，腰绳松系",
         tags=["sleeping kimono", "yukata", "barefoot", "loose fit",
               "sleepwear"],
         fields={"outfit": "sleeping kimono", "shoes": "bare feet"}),
    dict(id="hm_bathrobe", zh="白浴袍", cat="home",
         note="浴后，毛巾搭肩",
         tags=["bathrobe", "white bathrobe", "towel around neck", "barefoot",
               "wet hair"],
         fields={"top": "bathrobe", "socks": "barefoot",
                 "shoes": "bare feet", "cloth_color": "white"}),
    dict(id="hm_babydoll", min_level=2, zh="薄纱睡裙", cat="home",
         note="蕾丝薄纱，L2 起可用",
         tags=["babydoll", "sheer fabric", "lace", "panties", "barefoot"],
         fields={"n_fabric": "babydoll", "cloth_mat": "lace"}),
    dict(id="hm_oversized_shirt", zh="男友衬衫", cat="home",
         note="只一件白衬衫，领口敞开",
         tags=["oversized shirt", "white shirt", "bare legs", "barefoot",
               "unbuttoned collar"],
         fields={"top": "shirt", "socks": "barefoot", "shoes": "bare feet",
                 "cloth_state_sfw": "unbuttoned collar"}),
    dict(id="hm_hoodie_blanket", zh="卫衣 + 毛毯", cat="home",
         note="缩在毯子里，头发乱",
         tags=["hoodie", "blanket", "messy hair", "cozy", "bare legs"],
         fields={"top": "hoodie", "socks": "barefoot", "shoes": "bare feet"}),
    dict(id="hm_loungewear", zh="灰家居服", cat="home",
         note="卫衣 + 短裤 + 拖鞋",
         tags=["loungewear", "grey sweatshirt", "shorts", "slippers",
               "relaxed"],
         fields={"top": "sweater", "bottom": "shorts", "shoes": "slippers",
                 "cloth_color": "grey"}),
    dict(id="hm_nightgown", zh="白长睡裙", cat="home",
         note="蕾丝边，赤足",
         tags=["nightgown", "white nightgown", "lace trim", "barefoot",
               "long dress"],
         fields={"cloth_color": "white", "shoes": "bare feet"}),
    dict(id="hm_sleep_mask", zh="眼罩睡衣", cat="home",
         note="睡眼罩，躺床",
         tags=["pajamas", "sleep mask", "lying", "on bed", "blue pajamas"],
         fields={"cloth_color": "blue"}),
    dict(id="hm_apron_only", min_level=3, zh="裸围裙", cat="home",
         note="只一件围裙（L3 起）",
         tags=["apron only", "naked apron", "white apron", "barefoot",
               "from behind"],
         fields={"outfit": "apron only", "socks": "barefoot"}),

    # ── 泳装内衣 ────────────────────────────────────────────────────
    dict(id="sw_bikini_black", min_level=2, zh="黑比基尼", cat="swim",
         note="沙滩向，L2 起",
         tags=["black bikini", "bare legs", "sandals", "beach", "navel"],
         fields={"outfit": "bikini", "cloth_color": "black",
                 "shoes": "sandals"}),
    dict(id="sw_bikini_white", min_level=2, zh="白比基尼", cat="swim",
         note="白底细带",
         tags=["white bikini", "bare legs", "sandals", "beach", "navel"],
         fields={"outfit": "white bikini", "cloth_color": "white",
                 "shoes": "sandals"}),
    dict(id="sw_onepiece", min_level=1, zh="藏青连体泳衣", cat="swim",
         note="最保守的泳装，L1",
         tags=["one-piece swimsuit", "navy blue swimsuit", "barefoot",
               "poolside"],
         fields={"outfit": "one-piece swimsuit", "cloth_color": "blue",
                 "socks": "barefoot"}),
    dict(id="sw_competitive", min_level=1, zh="竞泳泳装", cat="swim",
         note="泳帽 + 泳镜",
         tags=["competitive swimsuit", "swim cap", "goggles", "barefoot",
               "swimming"],
         fields={"outfit": "competitive swimsuit", "socks": "barefoot"}),
    dict(id="sw_sports_bikini", min_level=2, zh="运动比基尼", cat="swim",
         note="运动剪裁 + 球鞋",
         tags=["sports bikini", "blue bikini", "white sneakers",
               "bare legs", "sporty"],
         fields={"outfit": "sports bikini", "shoes": "sneakers",
                 "cloth_color": "blue"}),
    dict(id="sw_lingerie_black", min_level=2, zh="黑蕾丝内衣", cat="swim",
         note="吊带袜，L2 起",
         tags=["lingerie", "black lace lingerie", "garter belt",
               "black thighhighs", "lace"],
         fields={"n_fabric": "lingerie", "socks": "black thighhighs",
                 "cloth_mat": "lace"}),
    dict(id="sw_lingerie_white", min_level=2, zh="白色内衣", cat="swim",
         note="纯白蕾丝",
         tags=["white lingerie", "lace", "panties", "bra", "garter straps"],
         fields={"n_fabric": "lingerie", "cloth_mat": "lace",
                 "cloth_color": "white"}),
    dict(id="sw_lingerie_red", min_level=3, zh="红色情趣内衣", cat="swim",
         note="束身衣 + 吊带袜，L3 起",
         tags=["red lingerie", "corset", "garter straps", "thighhighs",
               "red lace"],
         fields={"n_fabric": "corset", "socks": "thighhighs",
                 "cloth_color": "red"}),
    dict(id="sw_halter", min_level=1, zh="挂脖泳装", cat="swim",
         note="白挂脖，露背",
         tags=["halterneck", "white swimsuit", "bare legs", "backless",
               "poolside"],
         fields={"outfit": "halterneck", "cloth_color": "white"}),
    dict(id="sw_micro_bikini", min_level=3, zh="微型比基尼", cat="swim",
         note="细带微型款，L3 起",
         tags=["micro bikini", "string bikini", "bare legs", "navel",
               "beach"],
         fields={"outfit": "micro bikini", "socks": "barefoot"}),

    # ── 战斗机能 ────────────────────────────────────────────────────
    dict(id="cb_cyberpunk", zh="赛博机能", cat="combat",
         note="黑紧身 + 霓虹 + 机械件",
         tags=["cyberpunk", "techwear", "black bodysuit", "neon lights",
               "mechanical parts"],
         fields={"outfit": "cyberpunk", "top": "techwear",
                 "cloth_color": "black"}),
    dict(id="cb_tactical", zh="战术装", cat="combat",
         note="战术背心 + 工装裤 + 军靴",
         tags=["military uniform", "tactical vest", "cargo pants",
               "combat boots", "gloves"],
         fields={"outfit": "military uniform", "bottom": "cargo pants",
                 "shoes": "boots"}),
    dict(id="cb_ninja", zh="忍者装", cat="combat",
         note="黑紧身 + 面罩 + 手里剑",
         tags=["ninja", "black bodysuit", "face mask", "shuriken", "tabi"],
         fields={"outfit": "ninja", "socks": "tabi", "cloth_color": "black"}),
    dict(id="cb_knight", zh="骑士铠甲", cat="combat",
         note="板甲 + 护手 + 披风",
         tags=["knight", "armor", "gauntlets", "cape", "sword",
               "plate armor"],
         fields={"outfit": "knight", "cloth_color": "grey"}),
    dict(id="cb_plugsuit", zh="战斗紧身衣", cat="combat",
         note="插管紧身衣（plugsuit 不在词库，走 tags）",
         tags=["plugsuit", "bodysuit", "mechanical interface",
               "pilot suit", "skin tight"],
         fields={"cloth_mat": "latex", "cloth_color": "black"}),
    dict(id="cb_flightsuit", zh="飞行服", cat="combat",
         note="连体飞行服 + 束带",
         tags=["pilot suit", "flight suit", "utility belt", "zipper", "boots"],
         fields={"shoes": "boots", "cloth_color": "green"}),
    dict(id="cb_bodysuit", min_level=1, zh="黑紧身衣", cat="combat",
         note="连体 + 大腿靴",
         tags=["bodysuit", "black leotard", "black thighhighs", "boots",
               "skin tight"],
         fields={"outfit": "leotard", "socks": "black thighhighs",
                 "shoes": "thigh boots"}),
    dict(id="cb_race_queen", zh="赛车女郎", cat="combat",
         note="赛车服 + 阳伞",
         tags=["race queen", "umbrella", "leather jacket", "shorts", "boots"],
         fields={"outfit": "race queen", "shoes": "boots"}),
    dict(id="cb_witch", zh="魔女", cat="combat",
         note="黑帽 + 斗篷 + 法杖",
         tags=["witch", "black witch hat", "cloak", "black dress", "staff"],
         fields={"outfit": "witch", "cloth_color": "black"}),
    dict(id="cb_vampire", zh="吸血鬼", cat="combat",
         note="黑斗篷 + 红裙 + 尖牙",
         tags=["vampire", "black cape", "red dress", "fangs", "pale skin"],
         fields={"outfit": "vampire", "cloth_color": "red"}),

    # ── 主题扮演 ────────────────────────────────────────────────────
    dict(id="cp_maid", zh="女仆装", cat="cosplay",
         note="黑裙白围裙 + 头饰",
         tags=["maid", "black maid dress", "white apron", "maid headdress",
               "white thighhighs"],
         fields={"outfit": "maid", "socks": "white thighhighs",
                 "cloth_color": "black"}),
    dict(id="cp_bunny_girl", min_level=1, zh="兔女郎", cat="cosplay",
         note="黑连体 + 兔耳 + 白袖口",
         tags=["bunny girl", "black leotard", "bunny ears", "white cuffs",
               "black pantyhose"],
         fields={"outfit": "bunny girl", "socks": "black pantyhose",
                 "cloth_color": "black"}),
    dict(id="cp_nurse", zh="护士服", cat="cosplay",
         note="白裙 + 护士帽",
         tags=["nurse", "white nurse dress", "nurse cap",
               "white pantyhose", "syringe"],
         fields={"outfit": "nurse", "socks": "white pantyhose",
                 "cloth_color": "white"}),
    dict(id="cp_pe_teacher", zh="体育教师", cat="cosplay",
         note="运动服 + 哨子",
         tags=["track suit", "whistle", "red track jacket", "whistle around neck",
               "sporty"],
         fields={"outfit": "track suit", "top": "jacket",
                 "cloth_color": "red"}),
    dict(id="cp_devil", zh="小恶魔", cat="cosplay",
         note="黑连体 + 角 + 尾巴",
         tags=["devil", "black leotard", "horns", "tail", "wings"],
         fields={"cloth_color": "black"}),
    dict(id="cp_angel", zh="天使", cat="cosplay",
         note="白裙 + 羽翼 + 光环",
         tags=["angel", "white dress", "wings", "halo", "feathered wings"],
         fields={"cloth_color": "white"}),
    dict(id="cp_catgirl", zh="猫娘", cat="cosplay",
         note="黑紧身 + 猫耳 + 尾巴",
         tags=["catgirl", "black catsuit", "cat ears", "cat tail", "choker"],
         fields={"cloth_color": "black"}),
    dict(id="cp_santa", zh="圣诞装", cat="cosplay",
         note="红裙白毛边 + 圣诞帽",
         tags=["santa costume", "red dress", "white fur trim", "santa hat",
               "boots"],
         fields={"shoes": "boots", "cloth_color": "red",
                 "cloth_mat": "fur trim"}),
    dict(id="cp_gyaru", zh="辣妹", cat="cosplay",
         note="短裙 + 短上衣 + 美黑",
         tags=["gyaru", "tan skin", "blonde hair", "miniskirt", "crop top"],
         fields={"outfit": "gyaru", "bottom": "miniskirt", "top": "crop top"}),
    dict(id="cp_pirate", zh="海盗", cat="cosplay",
         note="三角帽 + 束身衣 + 长靴",
         tags=["pirate", "tricorn hat", "white shirt", "corset", "boots"],
         fields={"bottom": "corset", "shoes": "boots",
                 "cloth_color": "black"}),

    # ── 丝袜穿搭（v5.2.0 · 亮丝星点系 12 套）──────────────────────────
    # ⚠️ 词库里现成的 15 个 glittery 袜全部是 L0（袜子本身不进 NSFW）：
    #      连裤袜     black / white / pale pink / lavender / pale burgundy + " glittery pantyhose"
    #      过膝袜     同上 + " glittery thighhighs"
    #      吊带过膝   同上 + " glittery thighhighs", 另外把 "garter straps" 写成独立 tag
    #    吊带款的 `fields.socks` 故意只写 `garter straps`（会被 `_covered_by_tags` 判为
    #    已被更具体的 tag 覆盖 → 只闭麦不重设），否则正文里会出现两遍同一条袜子。
    dict(id="hs_star_ol_grey", verified=True, zh="丝袜 · 灰西装黑星点", cat="hosiery",
         note="最稳的丝袜通勤：灰西装 + 铅笔裙 + 黑星点连裤",
         tags=["grey blazer", "white blouse", "black pencil skirt",
               "black glittery pantyhose", "pointed toe high heels", "office lady"],
         fields={"outfit": "suit", "top": "blazer", "bottom": "pencil skirt",
                 "socks": "black glittery pantyhose",
                 "shoes": "pointed toe high heels", "cloth_color": "grey"}),
    dict(id="hs_star_ol_navy", verified=True, zh="丝袜 · 藏青套裙白星点", cat="hosiery",
         note="藏青套裙配白星点，反差干净",
         tags=["navy blue jacket", "white blouse", "navy pencil skirt",
               "white glittery pantyhose", "chunky heels", "office lady"],
         fields={"outfit": "suit", "top": "jacket", "bottom": "pencil skirt",
                 "socks": "white glittery pantyhose", "shoes": "chunky heels",
                 "cloth_color": "blue"}),
    dict(id="hs_star_school_black", verified=True, zh="丝袜 · 水手服黑星点", cat="hosiery",
         note="藏青水手 + 黑星点过膝，最像校规里的那款",
         tags=["navy sailor shirt", "navy pleated skirt",
               "black glittery thighhighs", "brown loafers", "serafuku"],
         fields={"outfit": "serafuku", "top": "sailor shirt",
                 "bottom": "pleated skirt", "socks": "black glittery thighhighs",
                 "shoes": "loafers", "cloth_color": "blue"}),
    dict(id="hs_star_school_pink", verified=True, zh="丝袜 · 白衬衫淡粉星点", cat="hosiery",
         note="淡粉过膝 + 粉百褶，甜但不腻",
         tags=["white shirt", "pink pleated skirt",
               "pale pink glittery thighhighs", "mary janes", "school uniform"],
         fields={"outfit": "school uniform", "top": "shirt",
                 "bottom": "pleated skirt",
                 "socks": "pale pink glittery thighhighs",
                 "shoes": "mary janes", "cloth_color": "pink"}),
    dict(id="hs_star_party_lavender", verified=True, zh="丝袜 · 小礼服薰衣草星点", cat="hosiery",
         note="黑小礼服 + 薰衣草星点，派对场",
         tags=["black cocktail dress", "lavender glittery pantyhose",
               "black high heels", "thin necklace", "sleeveless"],
         fields={"outfit": "dress", "socks": "lavender glittery pantyhose",
                 "shoes": "high heels", "cloth_color": "black"}),
    dict(id="hs_star_party_burgundy", verified=True, zh="丝袜 · 晚礼服酒红星点", cat="hosiery",
         note="黑长礼服 + 酒红星点 + 长手套",
         tags=["black evening gown", "pale burgundy glittery pantyhose",
               "black high heels", "elbow gloves", "backless"],
         fields={"outfit": "evening gown",
                 "socks": "pale burgundy glittery pantyhose",
                 "shoes": "high heels", "cloth_color": "black",
                 "cloth_state_sfw": "backless"}),
    dict(id="hs_star_garter_black", verified=True, zh="丝袜 · 黑星点吊带", cat="hosiery",
         note="白衬衫 + 黑短裙 + 黑星点吊带过膝",
         tags=["black glittery thighhighs", "garter straps", "white shirt",
               "black miniskirt", "black high heels"],
         fields={"top": "shirt", "bottom": "miniskirt",
                 "socks": "garter straps", "shoes": "high heels",
                 "cloth_color": "black"}),
    dict(id="hs_star_garter_white", verified=True, zh="丝袜 · 白星点吊带针织", cat="hosiery",
         note="米色针织 + 白百褶 + 白星点吊带",
         tags=["white glittery thighhighs", "garter straps",
               "beige knit sweater", "white pleated skirt", "white high heels"],
         fields={"top": "sweater", "bottom": "pleated skirt",
                 "socks": "garter straps", "shoes": "high heels",
                 "cloth_color": "white", "cloth_mat": "knitwear"}),
    dict(id="hs_star_home_pink", verified=True, zh="丝袜 · 男友衬衫淡粉星点", cat="hosiery",
         min_level=1,
         note="宽大衬衫 + 淡粉星点连裤，居家",
         tags=["pale pink glittery pantyhose", "oversized white shirt",
               "bare shoulders", "lace trim"],
         fields={"top": "shirt", "socks": "pale pink glittery pantyhose",
                 "cloth_color": "white",
                 "cloth_state_sfw": "oversized clothes"}),
    dict(id="hs_star_babydoll_lavender", zh="丝袜 · 薰衣草薄纱", cat="hosiery",
         min_level=2,
         note="薄纱睡裙 + 薰衣草星点（L2 起）",
         tags=["babydoll", "lavender glittery pantyhose", "lace",
               "sheer fabric", "barefoot"],
         fields={"n_fabric": "babydoll",
                 "socks": "lavender glittery pantyhose", "cloth_mat": "lace"}),
    dict(id="hs_star_garter_burgundy", zh="丝袜 · 酒红星点束身", cat="hosiery",
         min_level=3,
         note="红束身衣 + 酒红星点吊带（L3 起）",
         tags=["pale burgundy glittery thighhighs", "garter straps",
               "red corset", "thigh strap", "red lace"],
         fields={"bottom": "corset", "n_fabric": "corset",
                 "socks": "garter straps", "cloth_color": "red"}),
    dict(id="hs_star_lingerie_white", verified=True, zh="丝袜 · 白星点蕾丝内衣", cat="hosiery",
         min_level=2,
         note="白蕾丝内衣 + 白星点连裤（L2 起）",
         tags=["white lingerie", "white glittery pantyhose", "lace",
               "garter belt", "white lace"],
         fields={"n_fabric": "lingerie",
                 "socks": "white glittery pantyhose", "cloth_mat": "lace",
                 "cloth_color": "white"}),

    # ── 补进现有类（OL 3 / 校服 3 / 礼服 2）────────────────────────────
    dict(id="of_star_suit_burgundy", verified=True, zh="黑西装 · 酒红星点", cat="office",
         note="黑西装 + 酒红星点连裤 + 尖头高跟",
         tags=["black suit", "white blouse", "black pencil skirt",
               "pale burgundy glittery pantyhose", "pointed toe high heels"],
         fields={"outfit": "suit", "top": "blazer", "bottom": "pencil skirt",
                 "socks": "pale burgundy glittery pantyhose",
                 "shoes": "pointed toe high heels", "cloth_color": "black"}),
    dict(id="of_star_blouse_white", verified=True, zh="白衬衫 · 白星点吊带", cat="office",
         note="白衬衫 + 黑铅笔裙 + 白星点吊带",
         tags=["white shirt", "black pencil skirt", "white glittery thighhighs",
               "garter straps", "black high heels"],
         fields={"outfit": "office lady", "top": "shirt",
                 "bottom": "pencil skirt", "socks": "garter straps",
                 "shoes": "high heels", "cloth_color": "white"}),
    dict(id="of_star_flight_pink", verified=True, zh="空乘 · 淡粉星点", cat="office",
         note="藏青空乘套裙 + 淡粉星点连裤",
         tags=["flight attendant uniform", "navy blue jacket",
               "pale pink glittery pantyhose", "white blouse",
               "blue pencil skirt"],
         fields={"outfit": "flight attendant uniform", "top": "jacket",
                 "bottom": "pencil skirt",
                 "socks": "pale pink glittery pantyhose", "cloth_color": "blue"}),
    dict(id="sc_star_serafuku_winter", verified=True, zh="冬水手 · 黑星点", cat="school",
         note="长袖藏青水手 + 黑星点过膝",
         tags=["navy sailor shirt", "navy pleated skirt",
               "black glittery thighhighs", "brown loafers", "red neckerchief"],
         fields={"outfit": "serafuku", "top": "sailor shirt",
                 "bottom": "pleated skirt", "socks": "black glittery thighhighs",
                 "shoes": "loafers", "cloth_color": "blue"}),
    dict(id="sc_star_blazer_lavender", verified=True, zh="西装校服 · 薰衣草星点", cat="school",
         note="灰西装 + 藏青格裙 + 薰衣草星点",
         tags=["grey blazer", "lavender glittery pantyhose", "navy pleated skirt",
               "black loafers", "school uniform"],
         fields={"outfit": "school uniform", "top": "blazer",
                 "bottom": "pleated skirt",
                 "socks": "lavender glittery pantyhose", "shoes": "loafers",
                 "cloth_color": "grey"}),
    dict(id="sc_star_cardigan_white", verified=True, zh="开衫校服 · 白星点", cat="school",
         note="米开衫 + 白星点过膝",
         tags=["beige cardigan", "white shirt", "white glittery thighhighs",
               "brown loafers", "pleated skirt"],
         fields={"outfit": "school uniform", "top": "cardigan",
                 "bottom": "pleated skirt", "socks": "white glittery thighhighs",
                 "shoes": "loafers", "cloth_color": "brown"}),
    dict(id="fm_star_evening_black", verified=True, zh="晚礼服 · 黑星点", cat="formal",
         note="黑长礼服 + 黑星点连裤 + 长手套",
         tags=["black evening gown", "black glittery pantyhose",
               "black high heels", "elbow gloves", "backless"],
         fields={"outfit": "evening gown", "socks": "black glittery pantyhose",
                 "shoes": "high heels", "cloth_color": "black",
                 "cloth_state_sfw": "backless"}),
    dict(id="fm_star_cocktail_burgundy", zh="红小礼服 · 酒红星点", cat="formal",
         note="红小礼服 + 酒红星点吊带过膝",
         tags=["red cocktail dress", "pale burgundy glittery thighhighs",
               "garter straps", "black high heels", "sleeveless"],
         fields={"outfit": "dress", "socks": "garter straps",
                 "shoes": "high heels", "cloth_color": "red"}),
]


def _level_of(o: dict) -> int:
    """档位 = max(内容派生, 人工下限)。

    ⚠️ 为什么需要 `min_level`：`grade_nsfw` 不认识一批**实际很高档**的词 ——
       实测 `bikini` / `naked apron` / `apron only` / `garter straps` /
       `thigh boots` 全部返回 0。只看派生就会让「比基尼 + 裸围裙」漏进 SFW。
       这张下限表**只补判定表不认识的词**，不覆盖词库已经有的判定
       （露肩 / 吊带 / 荷叶边那类被判 L1 的，照旧听词库的）。
    """
    lv = 0
    for k, v in (o.get("fields") or {}).items():
        lv = max(lv, int(option_level(k, v)))
    for t in (o.get("tags") or []):
        lv = max(lv, int(tag_level(t)))
    lv = max(lv, int(o.get("min_level") or 0))
    return lv


def validate(items: list) -> list:
    """返回错误列表（空 = 全部合法）。"""
    errs = []
    cats = {c["id"] for c in CATEGORIES}
    seen = set()
    for i, o in enumerate(items):
        oid = str(o.get("id") or "")
        where = f"outfits[{i}] {oid or '(缺 id)'}"
        if not oid:
            errs.append(f"{where}: 缺 id")
            continue
        if not oid.replace("_", "").isalnum() or not oid.islower():
            errs.append(f"{where}: id 只能是小写字母数字下划线")
        if oid in seen:
            errs.append(f"{where}: id 重复")
        seen.add(oid)
        if not str(o.get("zh") or "").strip():
            errs.append(f"{where}: 缺中文名 zh")
        if o.get("cat") not in cats:
            errs.append(f"{where}: cat {o.get('cat')!r} 不在分类表里")
        tags = o.get("tags") or []
        if not isinstance(tags, list) or len(tags) < 3:
            errs.append(f"{where}: tags 至少 3 条（<3 条实测必翻车）")
        for t in tags:
            t = str(t)
            if t != t.lower():
                errs.append(f"{where}: tag {t!r} 必须小写")
            if "," in t:
                errs.append(f"{where}: tag {t!r} 不能含逗号")
            if len(t) > 80:
                errs.append(f"{where}: tag {t!r} 太长（>80）")
        if len(set(tags)) != len(tags):
            errs.append(f"{where}: tags 有重复项")
        for k, v in (o.get("fields") or {}).items():
            _sec, fld = field_by_key(k)
            if not fld:
                errs.append(f"{where}: 字段 {k!r} 不存在")
                continue
            opts = [en for _zh, en in fld.get("opts", [])]
            if v not in opts:
                errs.append(f"{where}: 字段 {k} 的取值 {v!r} 不是合法选项")
            if k in ("count", "role"):
                errs.append(f"{where}: 不得设置地基字段 {k}")
    return errs


def build(write: bool = True) -> dict:
    errs = validate(OUTFITS)
    if errs:
        raise SystemExit("校验失败：\n  " + "\n  ".join(errs))
    # 保留用户状态：active / overrides / scene_override
    # ⚠️ v5.9.0：以前这里只留 active —— 重跑会把用户设的袜子/鞋子覆盖项静默
    #    清掉（library_payload 的注释一直写着「重跑会保留」，实际是错的）。
    #    新加的「覆盖场景推荐服装」开关同理，绝不能被构建脚本抹掉。
    old_meta = {}
    if os.path.exists(LIB):
        try:
            with open(LIB, "r", encoding="utf-8") as fh:
                old_meta = (json.load(fh) or {}).get("_meta") or {}
        except Exception:
            old_meta = {}
    if not isinstance(old_meta, dict):
        old_meta = {}
    active = str(old_meta.get("active") or "")
    ov = old_meta.get("overrides")
    if not isinstance(ov, dict):
        ov = {}
    ids = {o["id"] for o in OUTFITS}
    if active not in ids:
        active = ""
    out = []
    for o in OUTFITS:
        out.append({
            "id": o["id"],
            "zh": o["zh"],
            "cat": o["cat"],
            "level": _level_of(o),
            "tags": list(o["tags"]),
            "fields": dict(o.get("fields") or {}),
            "drop": list(DROP_DEFAULT),
            "verified": bool(o.get("verified")),
            "note": str(o.get("note") or ""),
        })
    data = {
        "_meta": {
            "schema": SCHEMA,
            "updated_at": _now(),
            "count": len(out),
            "active": active,
            "categories": CATEGORIES,
            "overrides": {k: str(ov.get(k) or "").strip().lower()
                          for k in ("socks", "shoes")},
            "scene_override": bool(old_meta.get("scene_override")),
            "rules": [
                "level 由 tools/build_outfit_library.py 按内容派生，**不要手改**。",
                "启用中的一套写在 _meta.active；空串 = 关闭（回到随机服装）。",
                "角色自己启用了服装预设时，该角色优先用角色预设（见 README）。",
                "_meta.overrides / _meta.scene_override 是用户状态，重跑必须保留。",
            ],
        },
        "outfits": out,
    }
    if write:
        tmp = LIB + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, LIB)
    return data


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    d = build(write=not check)
    from collections import Counter
    c = Counter(o["cat"] for o in d["outfits"])
    lv = Counter(o["level"] for o in d["outfits"])
    print(f"[build_outfit_library] {len(d['outfits'])} 套 / {len(CATEGORIES)} 类")
    print("  分类:", dict(c))
    print("  档位:", dict(sorted(lv.items())))
    print("  " + ("--check 通过（未写盘）" if check else f"已写入 {LIB}"))
