# Elysia 2D complete review pack v3 — generation record

本文件记录 v3 的生成与确定性后处理规范。除特别注明外，均使用内置 ImageGen 的 reference-image workflow。参考图按各节描述作为输入，没有复制进本目录。

本记录是自包含文档。01、03、09 的最终 PNG 内容未在本轮重绘；删除旧 v1/v2 目录前，相关生成记录已迁入下面对应章节。

## 01 — Character turnaround

Mode: built-in ImageGen, reference-image workflow, stylized-concept.

~~~text
Use case: stylized-concept
Asset type: high-resolution professional 2D character turnaround / model sheet for Elysia AI
Primary request: Create one exceptionally clean, sharp seven-view full-body turnaround of the same adult anime character, Elysia, using the supplied images as identity and costume references. Show exactly these seven viewpoints: front, front three-quarter, right profile, back three-quarter, back, left profile, opposite front three-quarter.
Input images: Image 1 is the authoritative seven-angle anatomy, silhouette, hair length, costume construction, and view-direction reference; Image 2 is the highest-priority authority for her face shape and facial features; Image 3 is the official 2D rendering reference for facial identity and line treatment; Image 4 is the costume/color/detail authority; Image 5 is only a secondary application-rendering reference.
Scene/backdrop: solid very-light neutral warm gray RGB background, fully opaque, with clean subtle panel divisions; no transparency, no texture, no scenery.
Subject: the exact same recognizable Elysia in every view: gentle slightly angular oval face, centered symmetrical blue eyes, small straight centered nose, small natural closed mouth, long pointed elf ears, soft calm neutral expression, natural pale skin with NO blush or pink cheeks; long muted pastel rose-pink hair with controlled highlights, not neon and not overexposed; exact black, white, muted violet, antique-gold and cyan-gem outfit shown in the references.
Critical costume invariants: preserve the deliberate left/right asymmetry correctly in every viewpoint. The character's RIGHT arm (viewer-left in front view) has the black-and-gold long sleeve, muted-purple ruffled cuff, and bare hand. The character's LEFT arm (viewer-right in front view) has the white-and-gold forearm armor/cuff and a black glove. Her black/gold/violet head ornament is on the character's RIGHT side (viewer-left in front view). Preserve the rear black-and-white bow, long pink ponytail, asymmetric white and dark coat panels, stockings, thigh ornament, and gold-trimmed heels.
Style/medium: polished official-game-style 2D anime character design sheet; crisp controlled line art, flat-to-soft cel shading, restrained highlights, clean anatomically coherent hands, faithful costume construction, no painterly blur and no glossy AI airbrush look.
Composition/framing: wide landscape sheet, full figures from hair tip to shoe fully visible with generous margins. Use a spacious professional layout such as four large figures on the upper row and three large figures centered on the lower row so faces and costume details remain readable. Equal character scale, same neutral upright pose and body proportions in all views. No labels are required.
Lighting/mood: even neutral studio illumination for design inspection; no colored rim light; no romantic glow.
Color palette: muted pastel rose hair, natural pale skin, deep near-black brown, clean warm white, restrained lavender-violet, antique gold, small cyan accents.
Constraints: exactly seven complete figures and no duplicates; one consistent identity and body; faces clear and undistorted; eyes, nose and mouth centered and aligned; hands have five natural fingers; correct side-specific sleeve, glove, armor and head ornament in all seven views; opaque RGB background; maximum clarity and edge sharpness; no blush on any view.
Avoid: weapon or bow weapon; text; labels; logos; watermarks; UI; signatures; transparent background; white halos; red/magenta edge noise; extra limbs or fingers; crossed eyes; crooked nose; crooked mouth; asymmetrical facial distortion; baby/chibi proportions; neon pink hair; oversaturated colors; erotic posing; cleavage exaggeration; dramatic background effects.
~~~

## 02 — Activity states

Mode: built-in ImageGen, reference-image workflow, precise-object-edit.

~~~text
Use case: precise-object-edit
Asset type: corrected 4-by-2 Elysia AI activity-state atlas for final visual review
Primary request: Edit Image 1 into a corrected version of the same eight-cell atlas. Preserve the exact 4-column by 2-row layout, cell order, state meanings, face identity, low-saturation palette, pale opaque background, crop, costume and overall poses. Redraw and anatomically audit EVERY visible hand, wrist, cuff and forearm across all eight cells. The fixed row-major states remain: idle, listening, thinking, speaking; working/focused, waiting for approval, error/concerned, success/celebration.
Input images: Image 1 is the edit target and exact layout/state reference. Image 2 is the primary costume, identity and left/right asymmetry anchor. Image 3 is the highest-priority face-shape and natural-skin reference. Image 4 is the costume-detail and color authority. Image 5 is the official anatomical rotation and garment-construction authority.
Mandatory hand anatomy rule: every fully visible hand must contain exactly five digits total—one thumb plus four fingers—with correct joint structure, natural finger lengths, clean separation, and thumb pointing from the anatomically correct side of the palm. Partially hidden hands must still have a plausible five-digit construction, with occlusion rather than missing, duplicated or fused digits. No hand may have six fingers, repeated fingertips, forked fingers, merged fingers, floating digits, backward thumb, doubled palm or amorphous mitten anatomy.
Canonical side/material rule: Elysia's anatomical RIGHT arm/hand (viewer-left when front-facing) must connect continuously from a BLACK-AND-GOLD LONG SLEEVE through a MUTED-VIOLET RUFFLED CUFF to a completely BARE natural-skin right hand. Her anatomical LEFT arm/hand (viewer-right when front-facing) must connect continuously from the WHITE-AND-GOLD FOREARM ARMOR/GAUNTLET to a completely BLACK-GLOVED left hand. Skin tone must stop cleanly at the violet right cuff. Black glove color must stop cleanly at the left glove/gauntlet boundary. Never tint bare fingers black, never expose skin through the black glove, never put the black glove on the right hand, and never swap the two arm designs.
Cell-specific corrections and readable hand designs:
1 idle: bare right hand rests naturally over the black-gloved left hand; both hands remain individually readable and correctly layered.
2 listening: bare right hand is cupped beside the ear with exactly five correctly spaced digits; black-gloved left hand rests lower.
3 thinking: bare right index finger touches near the chin while its other four digits fold naturally; black-gloved left hand supports the opposite forearm.
4 speaking: REPLACE the erroneous six-finger open bare hand. Draw one anatomically correct bare right open palm with exactly one thumb and four fingers, all attached once to one palm; black-gloved left hand remains a separate restrained conversational gesture.
5 working/focused: each hand is separate and readable; bare right hand has natural five-digit precision gesture, black-gloved left hand is correctly attached to white-gold forearm armor.
6 waiting for approval: FIX the color contamination. Do not interlace or overlap the hands. Show the bare right hand and black-gloved left hand parallel and clearly separated by a small visible gap in a patient composed gesture, with sharp clean material boundaries and exactly five digits per fully visible hand.
7 error/concerned: bare right hand rests flat near the chest; black-gloved left hand remains lower and clearly separate.
8 success: one bare right fist and one black-gloved left fist, each with correct folded-finger construction, wrist direction and no extra knuckles.
Face/skin invariant: preserve the same gentle slightly angular oval face, centered blue eyes, straight centered small nose and aligned mouth. Natural pale skin only. No cheek blush, pink cheek patches, flushed nose or red face in any cell, including success.
Hair/color invariant: soft dusty low-saturation pastel rose hair; no neon, candy pink, magenta glow or overexposed highlights.
Style/medium: preserve Image 1's crisp official-game-style 2D anime line art, restrained cel shading and matte finish. Keep figures sharp at atlas scale.
Composition/framing: exact same 4x2 grid and one waist-up figure per cell; consistent scale; no hand, finger or prop crosses a cell boundary.
Constraints: change the hands, wrists and immediate cuff connections only as needed for correct anatomy/materials; preserve all other successful visual decisions from Image 1; exactly eight cells and eight characters; no text, labels, logos, watermark, UI, weapon, additional character or transparent background.
Avoid: six fingers; extra fingers; missing digits on a fully visible open hand; duplicated fingertips; fused fingers; backward thumbs; malformed palms; black color bleeding onto bare skin; skin-colored holes in black gloves; two bare hands; two black gloves; swapped sleeve sides; altered state order; blush; saturated pink hair; face redesign; changed grid; cropped hands.
~~~

## 03 — Facial expressions

Mode: built-in ImageGen, reference-image workflow, stylized-concept.

~~~text
Use case: stylized-concept
Asset type: production facial-expression reference atlas for a 2D desktop assistant
Primary request: Create one exceptionally clean, high-resolution 5 columns by 4 rows atlas containing exactly 20 equal head-and-shoulders portraits of the same Elysia character. Read left-to-right, top-to-bottom, the expressions are: (1) soft smile, (2) attentive, (3) focused, (4) speaking smile, (5) patient serious, (6) concerned, (7) happy, (8) gentle sad, (9) laugh, (10) wink, (11) playful, (12) shy blush, (13) surprised, (14) confused, (15) proud, (16) pout, (17) sleepy, (18) tender comfort, (19) solemn, (20) light tears.
Input images: Image 1 is the highest-priority identity anchor for exact face shape, eye placement, fringe, pointed ears, centered small nose and centered mouth; Image 2 is the official 2D face and soft low-saturation color reference; Image 3 is the official multi-angle outfit and head-ornament structure reference; Image 4 is the approved costume/color/detail master; Image 5 is layout reference only and must not be copied where it conflicts with this prompt.
Subject consistency: Every cell must depict the same young adult Elysia identity: softly tapered symmetrical face, natural light skin, low-saturation soft pink hair, clean pale blue eyes, pointed elf ears, canonical black-white-purple-gold collar and head ornament. Identical head scale, camera, crop, hairstyle, collar, ornament, face proportions, nose position and chin position in all 20 cells.
Expression construction: Communicate each expression through deliberate eyebrow shape, eyelids/eyes, and mouth shape. Do not use generalized cheek redness to signal emotion.
Blush rules, strict: Cell 12 shy blush is the ONLY cell with clearly visible blush. Cells 11 playful, 16 pout and 20 light tears may have only an extremely faint natural warmth, never obvious red cheeks. Every other cell—1 through 10, 13 through 15, and 17 through 19—has absolutely no cheek blush, no pink cheek circles, no flushed nose, and no red face.
Facial geometry, strict: Nose bridge, tiny nose tip, philtrum, mouth and chin all stay on the exact vertical centerline. No crooked noses, no slanted mouths unless the named expression intrinsically requires a subtle asymmetric smile, no drifting features, no duplicate facial parts, no extra nostrils, no misplaced mouth corners. Both eyes must remain correctly aligned and anatomically coherent except the intentional wink/closed/sleepy expressions.
Style/medium: polished official-game-like 2D anime character sheet, crisp controlled line art, restrained cel shading, subtle gradients, no painterly AI texture, no glossy plastic look.
Composition/framing: exact regular 5x4 grid, 20 complete cells, uniform thin neutral dividers, every portrait centered with consistent padding and no cropping of head ornament or chin.
Scene/backdrop: one flat pale warm-neutral RGB background in every cell, fully opaque, no transparency.
Color palette: soft restrained canonical colors; hair must be pale muted pink rather than neon or saturated magenta; skin must remain natural and unflushed except the explicit blush cells.
Constraints: high edge clarity; clean eyes, nose, lips and chin; no text, numbers, labels, logos, signatures, watermarks, UI, decorative symbols, border ornaments, props, weapons, extra characters or background scenery.
Avoid: habitual blush, red cheeks, pink nose, oversaturated hair, identity drift, malformed pupils, crossed eyes, crooked nose, off-center mouth, warped jaw, inconsistent cell size, missing cells, text, watermark.
~~~

## 04 — Facial rig atlas

Mode: built-in ImageGen, reference-image workflow, stylized-concept.

~~~text
Use case: stylized-concept
Asset type: high-clarity production facial-rig reference atlas for a 2D desktop assistant
Primary request: Create ONE landscape PNG containing exactly 19 front-facing head-and-shoulders portraits of the SAME Elysia, arranged as FOUR separate horizontal bands with generous vertical breathing room. This replaces the crowded nine-face row in Image 5.

Exact layout and order:
BAND 1, top: exactly 5 equally sized cells, left to right mouth shapes CLOSED, SMALL, MEDIUM, WIDE, A.
BAND 2: exactly 4 equally sized cells, left to right mouth shapes E, I, O, U.
BAND 3: exactly 6 equally sized cells, left to right eye shapes OPEN, HALF-OPEN, CLOSED, HAPPY CRESCENT, WIDE, WORRIED.
BAND 4, bottom: exactly 4 equally sized cells, left to right eyebrow shapes RELAXED, FOCUSED, RAISED, CONCERNED.
There must be 5 + 4 + 6 + 4 = exactly 19 portraits. Do not add or omit cells. Different bands may have different column counts. Center the four-cell bands with balanced side gutters. Use reasonable equal cell widths within each band. Do not force all bands into one common column grid.

Input images: Image 1 is the highest-priority identity anchor for Elysia's face shape, eye placement, fringe, pointed ears, small centered nose and centered mouth. Image 2 is the official 2D face and soft low-saturation color reference. Image 3 is the canonical outfit and head-ornament structure reference. Image 4 is the approved costume/color/detail master. Image 5 is the previous rig atlas used only as a content and identity reference; fix its crowded top band and clipped ornament.

Subject and identity invariants: All 19 portraits depict the exact same young adult Elysia identity, perfectly front-facing at zero yaw, zero pitch and zero roll. Preserve identical head size, face outline, camera distance, hair silhouette, fringe, pointed ears, head ornament, collar, pale blue eyes, nose, jaw, chin and skin tone throughout. Natural light skin. Pale muted low-saturation pink hair, never neon or magenta. Canonical restrained black-white-purple-gold collar and ornament.

Critical facial geometry: In every cell, the nose bridge, tiny minimalist centered nose mark, philtrum, mouth center and chin lie on one exact vertical center axis. Both eyes are level and equally spaced. No crooked nose, sideways nose wedge, extra nostril, drifting mouth, slanted lips, asymmetric jaw, crossed eye, warped pupil, duplicated feature, or mismatched mouth corner.

Mouth bands invariants: In Bands 1 and 2, the head, nose, eyes, relaxed eyebrows, jaw and chin are visually identical; change ONLY the mouth. Every mouth is symmetrical and centered directly beneath the nose.
CLOSED = a centered neutral closed line, not a smile.
SMALL = a tiny narrow opening.
MEDIUM = a modest oval opening.
WIDE = a broad open mouth.
A = a tall open oval.
E = a broad horizontal opening.
I = a very narrow horizontal opening.
O = a clear round circle.
U = a smaller rounded circle.
Do not use an X mouth and do not substitute smiles for phonemes.

Eye band invariants: In Band 3, use one identical neutral centered closed mouth, identical nose and identical relaxed eyebrows; change ONLY eyes and eyelids. OPEN, HALF-OPEN, CLOSED, HAPPY CRESCENT, WIDE and WORRIED must be clearly distinct while both eyes remain aligned.

Eyebrow band invariants: In Band 4, use identical open eyes, identical neutral centered mouth and identical centered nose; change ONLY the eyebrows. RELAXED, FOCUSED, RAISED and CONCERNED must be clearly distinct despite the fringe.

Blush rule, absolute: All 19 portraits have completely clear natural skin. No blush, no red cheeks, no cheek stripes, no pink circles, no flushed nose, no rosy face.

Style/medium: crisp polished official-game-like 2D anime character-sheet rendering; precise controlled line art; restrained cel shading; subtle clean gradients; maximum face sharpness; no painterly AI texture and no glossy plastic finish.

Composition/framing: four clearly separated horizontal bands on one landscape canvas. Thin neutral dividers. Every portrait centered inside its own cell with generous internal padding. Leave a clearly visible safe margin above every head ornament and below every chin/collar. No hair, ornament, ear, chin or collar may touch or cross a canvas edge or divider. Heads must not appear squeezed together.

Scene/backdrop: one flat pale warm-neutral RGB background, fully opaque, no transparent pixels and no alpha-edge noise.
Constraints: no text, labels, letters, numbers, legends, logos, signatures, watermarks, UI, icons, decorations, props, weapons, extra characters or scenery.
Avoid: crowded rows, clipped ornament, head touching border, blush, red skin, oversaturated hair, identity drift, malformed anatomy, crooked nose, off-center mouth, diagonal lips, inconsistent head angle, missing cells, extra cells, text or watermark.
~~~

## 05 — Chibi stickers

Mode: built-in ImageGen, reference-image workflow, stylized-concept. Each row was generated independently at high resolution; the common block below was followed by one row block.

### Common block

~~~text
Use case: stylized-concept
Asset type: high-resolution single-row production strip for Elysia chibi conversation stickers
Primary request: Generate exactly FIVE separate Elysia chibi illustrations in ONE horizontal row of exactly five equal panels. This is one row of a larger 5x5 set, so produce only the five requested actions below, left to right, with no extra figures.
Input images: Image 1 is layout/action continuity reference only; correct its hand defects and do not copy missing fingers. Image 2 is the HIGHEST-PRIORITY style/proportion reference: understated hand-drawn chibi, oversized rounded head, compact body, thick but clean soft dark line, flat low-saturation color, one gentle shadow tier, matte finish. Do not copy its alternate outfit or hat. Image 3 is authoritative for canonical outfit construction and anatomical left/right equipment. Image 4 anchors Elysia's face, blue eyes, centered small nose and mouth. Image 5 supports canonical costume details and muted palette.
Scene/backdrop: one solid very pale warm ivory RGB background; five equal panels separated by thin warm-gray vertical lines; no transparency, cutout halo, sticker outline, glow, scenery, text, decorative header, footer, texture, noise or blank bands.
Subject: the same recognizable chibi Elysia in all five panels: pale low-saturation dusty-pink hair, soft blue eyes, pointed elf ears, black-and-antique-gold head ornament, faithfully simplified black, cream-white, muted-violet and antique-gold canonical outfit.
Critical anatomical equipment: Elysia's anatomical RIGHT arm/hand, viewer-left when front-facing, has the dark black-brown/gold long sleeve and muted-violet ruffled cuff ending in a BARE skin-colored hand. Elysia's anatomical LEFT arm/hand, viewer-right when front-facing, has the cream-white/gold forearm armor ending in a BLACK GLOVE. Never mirror or swap these sides.
ABSOLUTE HAND QUALITY RULE: every complete visible hand must be anatomically normal and contain exactly FIVE digits: one thumb plus four distinct fingers. Every open hand must show five clearly separated readable silhouettes—thumb, index, middle, ring and little finger—with correct length order, webbing and joint direction. Every curled, fist, heart, pointing or gripping hand must physically account for a thumb plus four separate curled-finger/knuckle forms; never a mitten. Make hands slightly larger and cleaner than v2, fully against pale background, never hidden in or merged with pink hair. Every black glove has an unbroken near-black outer contour and a pale negative-space gap wherever it approaches hair.
Style/medium: closely match Image 2's quiet hand-drawn Q-version feel; soft varied dark line, flat shapes, restrained cel shadow, minimal highlights, low saturation, matte finish; not glossy AI sticker art.
Composition/framing: five large bust-to-compact-body chibis at equal scale, one per panel, generous margins; prioritize large readable hands and faces; all hands, ears and props fully inside their panel; no crossing dividers.
Blush policy: only an explicitly shy panel may show visible blush. Heart, apology or sad may have at most extremely faint natural warmth. Every other panel has clear natural skin with no cheek stripes, red cheeks, pink nose or flushed face.
Face quality: centered symmetrical eyes unless intentionally winking, centered tiny nose, centered mouth, no drifting or distorted features.
Constraints: exactly five panels and five characters; same identity/outfit/proportions; every visible complete hand exactly five digits; correct bare-right-hand / black-gloved-left-hand asymmetry; no labels, letters, numbers, logos, watermarks, UI, weapons or extra people.
Avoid: any three-finger or four-finger hand, six fingers, fused digits, missing thumb, mitten fist, deformed palm, mirrored gloves, black glove merging into hair, cropped hands, vivid candy pink, neon, plastic gloss, heavy gradients, excessive highlights, random blush, alternate costume or hat.
~~~

### Row 1

~~~text
Panel actions, left to right:
1. HELLO WAVE: front-facing friendly smile; anatomical RIGHT bare hand raised toward viewer in a clear open wave with all five separated digits; anatomical LEFT black-gloved hand open and lower beside chest, also five digits.
2. WELCOME BACK: warm open-arm welcome; both hands fully visible against background, bare right palm and black-gloved left palm, each exactly five separated digits.
3. GOOD MORNING: cheerful stretch; both arms raised but separated from head and hair; bare right and outlined black-gloved left open hands, each exactly five digits.
4. GOOD NIGHT: sleepy pose leaning on a simple pale pillow; bare right hand rests visibly along one pillow edge and black-gloved left hand along the other, each anatomically five-digited and not hidden.
5. GOODBYE: friendly wave distinct from panel 1 by gentle turning posture; bare right hand open with five separated digits; black-gloved left hand open near chest with five digits.
Additional symbols: none except one tiny restrained sleep puff in panel 4.
~~~

### Row 2

~~~text
Panel actions, left to right:
1. GENTLE SMILE: calm open eyes; both relaxed hands held low in front and fully visible, bare right and black-gloved left, each with five natural digits.
2. JOYFUL LAUGH: open-mouth genuine laugh; hands held away from face and hair, both open enough to show all five digits, no thumbs-up.
3. PLAYFUL WINK: intentional one-eye wink; bare right hand makes a V gesture where thumb and the other two curled fingers remain clearly accounted, black-gloved left hand open with five digits.
4. HEART: both hands form one small heart at chest; each hand still has one thumb plus four anatomically distinct fingers, no fused central mass; hands remain separated by clear linework.
5. SHY: the only clearly blushing panel; both hands beside but not covering cheeks, palms readable against background, bare right and black-gloved left each exactly five fingers.
Additional symbols: no floating hearts, sparkles or decorative marks.
~~~

### Row 3

~~~text
Panel actions, left to right:
1. PRAISE: cheerful wink and anatomical RIGHT bare hand giving a clear thumbs-up; its thumb is extended and all four curled fingers are separately readable by four knuckles; anatomical LEFT black-gloved hand open with exactly five digits.
2. CELEBRATE: both hands raised and open, bare right and outlined black-gloved left, each showing exactly five separated digits; no sparkles or confetti.
3. THANK YOU: gentle small bow; hands held separately over chest rather than fused or palm-to-palm; both complete hands visible and each exactly five digits.
4. CHEER UP: one small bare-right fist with one thumb and four individually described knuckles, black-gloved left hand open with five digits; no symbols.
5. THINKING: bare right index touches chin while the same hand's thumb and three other fingers remain clearly accounted; black-gloved left hand open at lower chest with all five digits.
Additional symbols: none.
~~~

### Row 4

~~~text
Panel actions, left to right:
1. CONFUSED: questioning head tilt; both palms open at different heights against clear background, bare right and black-gloved left each exactly five separated digits; no question-mark symbol.
2. SURPRISED: widened eyes and small centered mouth; both open hands near shoulders, each exactly five separated digits and away from hair.
3. CONCERNED: softened worried brows; bare right hand rests over chest with five readable digits, black-gloved left palm open beside body with five digits.
4. COMFORT HUG: both arms reach gently toward viewer; foreshortened open bare-right and black-gloved-left palms are large, anatomically correct and each has five separated digits; neither overlaps hair.
5. SAD TEAR: one restrained tear; bare right index gently near tear while its thumb and remaining three fingers are accounted; black-gloved left hand open below with five outlined digits. At most trace natural warmth, no red cheeks.
Additional symbols: none.
~~~

### Row 5

~~~text
Panel actions, left to right:
1. POUT: centered cute pout; do not cross arms or hide hands; both hands open low at sides, bare right and black-gloved left each exactly five digits.
2. SORRY: small apologetic bow; both open hands held separately in front, each exactly five visible digits, no clasping/fusion; only extremely faint natural warmth.
3. WORKING: focused expression; anatomical RIGHT BARE hand holds a simple pen with a normal five-digit writing grip, and anatomical LEFT BLACK-GLOVED hand supports a simple paper with thumb plus four fingers all physically coherent and darkly outlined.
4. PLEASE REVIEW: anatomical RIGHT bare hand and anatomical LEFT black-gloved hand hold opposite edges of a simple paper; each has one thumb plus four coherent fingers, no fused digits; warm neutral smile.
5. WARNING/ERROR: serious caution expression; anatomical RIGHT bare hand faces viewer as a large stop palm with exactly five separated digits; anatomical LEFT black-gloved hand remains open and visibly five-digited at chest; no warning symbol.
Additional symbols: none; papers are blank with no text or marks.
~~~

## 06 — UI illustrations

Mode: built-in ImageGen, reference-image workflow, precise-object-edit.

~~~text
Use case: precise-object-edit
Asset type: corrected 4-by-4 Elysia AI product-state UI illustration atlas for final visual review
Primary request: Edit Image 1 into a corrected version of the same sixteen-cell atlas. Preserve its exact 4-column by 4-row grid, cell order, state meanings, face identity, low-saturation palette, pale opaque background, props, crop and overall poses. Redraw and anatomically audit EVERY visible hand, wrist, cuff and forearm across all sixteen cells. Fixed row-major concepts remain: assistant avatar, chat welcome, backend waiting, disconnected; no chats, no projects, no memory, no sources; indexing/reading, no search results, microphone unavailable, fatal error/reload; reminder, approval required, task success, setup complete.
Input images: Image 1 is the edit target and exact 4x4 composition/concept reference. Image 2 is a corrected hand-and-arm asymmetry reference in the same rendering language. Image 3 is the primary canonical costume, identity and left/right asymmetry anchor. Image 4 is the costume-detail and color authority. Image 5 is the highest-priority facial identity and natural-skin reference.
Mandatory hand anatomy rule: every fully visible hand must contain exactly five digits total—one thumb plus four fingers—with correct joints, natural relative lengths, clean separation and anatomically correct thumb direction. A partially occluded hand must remain structurally plausible, with digits hidden by the prop or other hand rather than missing, duplicated or fused. No hand may have six fingers, repeated fingertips, forked fingers, merged fingers, floating digits, backward thumb, doubled palm, collapsed knuckles or amorphous mitten anatomy.
Canonical side/material rule: Elysia's anatomical RIGHT arm/hand (viewer-left when front-facing) connects continuously from the BLACK-AND-GOLD LONG SLEEVE through a MUTED-VIOLET RUFFLED CUFF to a completely BARE natural-skin right hand. Her anatomical LEFT arm/hand (viewer-right when front-facing) connects continuously from the WHITE-AND-GOLD FOREARM ARMOR/GAUNTLET to a completely BLACK-GLOVED left hand. Maintain these anatomical sides through turns, crossing gestures and props. Skin tone stops cleanly at the violet right cuff; the black glove stops cleanly at the left gauntlet. Never place a black glove on the right hand, never leave the left hand bare, never create two black gloves or two bare hands, and never allow skin/glove colors to bleed into each other.
Global prop interaction: when holding the speech bubble, folder, page, book, magnifying glass, bell or clipboard, fingers must wrap around an edge or handle naturally; thumbs oppose the fingers; props must not erase, merge with or pass through the hand. Simplify a hand pose if necessary instead of inventing an extra digit.
Required cell-specific repairs:
Cell 2 chat welcome: extended bare right hand has exactly one thumb and four fingers, naturally foreshortened, connected to the dark sleeve and violet cuff.
Cell 3 backend waiting: if both hands appear, show one bare right hand and one black-gloved left hand with clean separation, not two gloves.
Cell 5 no chats and Cell 6 no projects: bare right hand supports the viewer-left side of the prop; black-gloved left hand supports the viewer-right side; each grip has plausible five-digit construction.
Cell 8 no sources: preserve the page but ensure the viewer-left hand is bare and the viewer-right hand is black-gloved.
Cell 9 indexing/reading: preserve the book; use a bare right hand on viewer-left and black-gloved left hand on viewer-right, with correct page-edge grips.
Cell 10 no search results: magnifying-glass handle is held by the bare anatomical right hand connected to the dark sleeve/violet cuff; show a natural thumb-and-four-finger grip.
Cell 11 microphone unavailable: FIX the apparent two-black-glove error. Show a clearly visible bare anatomical right hand connected to the dark sleeve/violet cuff and a clearly visible black-gloved anatomical left hand connected to white-gold armor. Keep the hands separate rather than interlaced.
Cell 12 fatal error/reload: the forward pointing gesture must obey its side. Either make the existing viewer-right/white-armored pointing hand a BLACK-GLOVED anatomical left hand, or move the gesture to the viewer-left bare anatomical right hand connected to the dark sleeve. It must have one clear index finger plus four naturally folded digits, not a malformed pointing hand.
Cell 13 reminder: REPLACE the malformed bell-holding hand. The raised bell is held by the bare anatomical right hand connected to the black-gold sleeve and violet cuff. Exactly five digits: one thumb opposing four fingers wrapped naturally around the bell handle; no extra fingertip, fork or fused finger. The other visible anatomical left hand remains a black glove.
Cell 14 approval required: clipboard is held by one bare right hand on viewer-left and one black-gloved left hand on viewer-right; no fingers merge into the clipboard.
Cell 15 task success: raised bare right fist and black-gloved left thumbs-up both have correct folded-digit anatomy.
Cell 16 setup complete: black-gloved left thumbs-up has a single thumb and four folded fingers with coherent palm and wrist.
Face/skin invariant: preserve the same gentle slightly angular oval face, centered blue eyes, straight centered nose and aligned mouth. Natural pale skin with no unexplained cheek blush, pink patches, flushed nose or red face in any cell.
Hair/color invariant: dusty low-saturation pastel rose hair; no neon pink, candy-pink saturation or magenta glow.
Style/medium: preserve Image 1's crisp restrained official-game-style 2D anime product illustration, controlled cel shading and matte finish.
Composition/framing: exact same 4x4 matrix, one waist-up vignette per equal cell, same state order, same symbolic props and consistent scale; no hand or prop crosses a grid boundary.
Constraints: change hands, wrists and immediate cuff/gauntlet connections only as necessary; preserve all other successful features; exactly sixteen cells and sixteen depictions; no text, labels, letters, logos, watermark, weapon, extra character or transparent background.
Avoid: six fingers; extra or missing fully visible digits; duplicated fingertips; fused hands; backward thumbs; malformed grips; hand-prop intersections; black color bleeding into bare skin; bare skin leaking through glove; two black gloves; two bare hands; reversed sleeves; changed prop inventory; reordered cells; blush; saturated pink hair; face redesign; changed grid; cropped hands.
~~~

## 07 — Desktop-pet key poses

Mode: built-in ImageGen, reference-image workflow, stylized-concept. Each row was generated independently at high resolution; the common block below was followed by one row block.

### Common block

~~~text
Use case: stylized-concept
Asset type: high-resolution single-row production strip for Elysia chibi desktop-pet animation key poses
Primary request: Generate exactly FOUR full-body Elysia chibi illustrations in ONE horizontal row of exactly four equal panels, only the requested actions below from left to right.
Input images: Image 1 is action/layout continuity reference only; correct every hand defect and never copy missing outlines or fingers. Image 2 is the HIGHEST-PRIORITY style/proportion reference: understated hand-drawn chibi, rounded oversized head, compact full body, thick clean soft dark line, flat low-saturation colors, one gentle shadow tier and matte finish; do not copy its alternate outfit or hat. Images 3-5 are authoritative for front/back/side canonical outfit construction, anatomical left/right equipment, hair, rear bow, coat tails, stockings and shoes.
Scene/backdrop: clean solid very pale warm ivory RGB from edge to edge; four equal panels separated by thin warm-gray vertical lines; no transparency, cutout halo, decorative border, header/footer, texture, noise, text or scenery.
Subject: identical recognizable full-body chibi Elysia in all four panels: low-saturation dusty-pink hair, blue eyes, pointed ears, black/antique-gold head ornament, faithfully simplified canonical cream-white, near-black, muted-violet and antique-gold outfit.
Critical side equipment: anatomical RIGHT arm/hand, viewer-left when front-facing, has dark black-brown/gold sleeve plus violet ruffled cuff ending in a BARE skin-colored hand. Anatomical LEFT arm/hand, viewer-right when front-facing, has cream-white/gold forearm armor ending in a BLACK GLOVE. Preserve those same anatomical sides through turns; never mirror or swap.
ABSOLUTE HAND QUALITY: every complete visible hand has exactly five digits—one thumb plus index, middle, ring and little finger. Open hands show five individually separated silhouettes with correct length and joints. Curled, pointing or gripping hands still account for thumb plus four fingers/knuckles, never a mitten. Slightly enlarge hands for inspection without caricature. Keep hands outside the pink-hair silhouette and against pale background whenever possible.
BLACK GLOVE EDGE RULE: every black-gloved left hand has a continuous clean near-black exterior outline around wrist, palm, thumb and all four fingers. Leave a visible pale-background gap between glove and pink hair. Never allow glove fill or outline to disappear into hair, sleeve or background.
Style: closely match Image 2's quiet hand-drawn Q-version feeling; soft varied dark line, flat matte color, restrained single cel shadow, minimal highlights, low saturation, no glossy AI-sticker finish.
Composition: four equal panels; one complete full-body figure per panel, large consistent scale, hair tip through shoes fully contained, feet on common baseline except floating/dragged actions; hands large enough to inspect; no crossing dividers.
Blush: no blush, red cheek patches, cheek stripes, flushed nose or red face in any panel.
Face/body: stable centered facial features, coherent limbs, believable joints and feet.
Constraints: exactly four panels/figures; fixed action order; correct bare-right-hand / black-gloved-left-hand equipment; every visible complete hand five digits; no text/logos/UI/weapons/cursors/extra people.
Avoid: missing/extra/fused fingers, 3- or 4-finger hands, mitten fists, missing glove outline, glove merging with hair, mirrored equipment, cropped hand/body, vivid pink, neon, plastic gloss, random blush, alternate costume or visual noise.
~~~

### Row 1

~~~text
Actions:
1 SPAWN WAVE — appearing with friendly wave; anatomical RIGHT bare hand raised open with five separated digits; anatomical LEFT black-gloved palm open lower at side with five outlined digits.
2 IDLE BREATHING — calm standing pose; both hands relaxed but fully visible away from hair, bare right and black-gloved left, each exactly five digits.
3 BLINK — same calm standing pose with eyes briefly closed; both complete hands visible at sides, exactly five digits each.
4 CURSOR LOOK — head and upper torso turn naturally toward an implied offscreen cursor; no visible cursor; both hands remain visible, bare right and strongly outlined black-gloved left, five digits each.
Symbols: none.
~~~

### Row 2

~~~text
Actions:
1 WALK/FLOAT — clear forward locomotion silhouette; arms swing away from hair so bare right and outlined black-gloved left hands remain visible with five digits each.
2 SIT — comfortable seated full-body pose; both hands rest openly beside knees rather than underneath body, each exactly five digits.
3 SLEEP — seated/curled sleep with eyes closed; bare right rests openly on one knee and black-gloved left on the other, five digits each, glove fully outlined and separate from hair; allow one tiny sleep puff only.
4 CLICK REACTION — surprised but friendly recoil; both open hands near shoulders but outside hair silhouette, bare right and black-gloved left each exactly five separated digits.
No other symbols.
~~~

### Row 3

~~~text
Actions:
1 DRAGGED, critical repair target — body safely suspended from an unseen point above, feet dangling, mildly startled. The anatomical RIGHT bare hand extends to the viewer-left against empty ivory, open with exactly five separated digits. The anatomical LEFT BLACK-GLOVED hand extends to the viewer-right against empty ivory, open with exactly five separated digits. Move both hands completely OUTSIDE the pink hair silhouette. Put a visible ivory gap between black glove and every hair strand. Draw a continuous thick dark outline around the glove's wrist, palm, thumb, index, middle, ring and little finger. No cursor, rope or visible dragging hand.
2 DRAG RELEASE/LANDING — small balanced crouch after safe landing; bare right palm and outlined black-gloved left palm brace separately against background/ground, exactly five digits each, not under hair.
3 OPEN CHAT INVITATION — upright welcoming pose with both arms open; bare right and black-gloved left palms each have five separated digits; no UI window.
4 NOTIFICATION ATTENTION — attentive turn; both hands fully visible outside hair, bare right and outlined black-gloved left, five digits each; no notification icon.
Symbols: none.
~~~

### Row 4

~~~text
Actions:
1 LISTENING — bare anatomical-right hand cups near ear without touching hair; thumb plus four fingers clearly visible; outlined black-gloved left hand open at side with five digits.
2 THINKING — bare right index touches chin while that hand's thumb and other three fingers remain accounted; black-gloved left palm open away from hair with five digits.
3 SPEAKING — natural small open mouth and open conversational gesture; bare right palm and black-gloved left palm both visible with exactly five digits.
4 WORKING — anatomical RIGHT BARE hand holds a simple pen with a coherent five-digit writing grip; anatomical LEFT BLACK-GLOVED hand supports blank paper with thumb plus four fingers physically present and a continuous dark outline; glove separated from hair.
No symbols; paper blank.
~~~

## 08 — Layer-separation guide

Mode: built-in ImageGen, reference-image workflow, background-extraction, followed by deterministic alpha processing.

~~~text
Use case: background-extraction
Asset type: corrected transparent-background layer-separation visual guide for Elysia AI
Primary request: Recreate the same organized component inventory and approximate placement as the target image: facial variants and facial parts, front and back hair groups, full front character, front/back torso pieces, left/right sleeves and hands, rear bow, coat panels, stockings, shoes, head ornament and small costume accessories. Preserve the recognizable identity, canonical costume, low-saturation colors and clean spacing. Improve the cutout quality rather than redesigning the sheet.
Input images: Image 1 is the target inventory and layout reference. The remaining references define canonical face, costume construction, front/back/side silhouette, rear bow, hands and colors.
Background and alpha: output a true transparent PNG. Every foreground pixel must be completely opaque alpha 255 and every background pixel completely transparent alpha 0. Use hard binary alpha only. Do not create antialiased semi-transparent edge pixels, feathering, translucent fringe, white matte, pink halo, glow, shadow or hidden opaque background.
Edge quality: create one crisp, deliberate closed contour around every component. Preserve fine pointed hair and fabric shapes with solid interior color to their contour. Do not leave loose white pixels, fuzzy fringe, low-alpha specks, partially erased edges or unrelated fragments.
Hand rule: any complete visible hand must be anatomically normal with exactly one thumb and four fingers. Preserve Elysia's anatomical-right bare hand and anatomical-left black glove rather than mirroring them.
Composition: one landscape component sheet with generous gaps so no component touches another. Keep the useful inventory of the target; no text, labels, grid, logos or decorative scenery.
Avoid: translucent edge, soft alpha, white outline, gray haze, pink fringe, missing piece, duplicated piece, fused components, extra finger, incorrect glove side, costume redesign, oversaturated hair or opaque background.
~~~

The raw ImageGen result still contained 255 distinct alpha values. The final file therefore applies this fixed post-process:

~~~text
For every pixel:
    if alpha >= 192: alpha = 255
    else: alpha = 0
    if alpha == 0: red = green = blue = 0
~~~

## 09 — Color and detail master

No new ImageGen call was made for this sheet during the final revision. The project owner marked the existing sheet as acceptable, so the PNG was retained byte-for-byte. Its SHA-256 remains `759F0CE419955B137F414E69DEFEC43CDF9D200DEE223F3A0E9F260006ED8641`.

## Targeted corrective edits after enlarged review

The following repairs were generated as individual high-resolution cell patches and composited deterministically into the existing atlas. Unlisted cells were not regenerated.

### 02 cells r1c2, r1c4 and r2c2

~~~text
Use case: precise-object-edit
Asset type: isolated hand repair patch for the existing activity-state atlas
Primary request: Change only the specified hand area; preserve the exact character identity, face, hair, costume, pose, crop, scale, palette and background.
For r1c2 listening: repair only the raised bare anatomical RIGHT hand beside the ear. Draw exactly one thumb plus four fingers, with four individually separated correctly jointed fingers. Keep the listening palm orientation. The bare hand must connect to the black-and-gold right sleeve and violet cuff; the anatomical LEFT hand remains a black glove.
For r1c4 speaking: repair only the extended bare anatomical RIGHT hand on viewer-left. Draw exactly five normal digits—one thumb and four fingers—clearly separated in the existing open presenting gesture. Preserve the black-and-gold sleeve and violet cuff. The viewer-right anatomical LEFT hand remains black-gloved with white-and-gold armor.
For r2c2 waiting: repair only the two center hands. Separate the bare anatomical RIGHT hand and black-gloved anatomical LEFT hand completely, leaving a clearly visible strip of pale background or costume between them. Each hand must retain a plausible five-digit construction and its correct sleeve/gauntlet.
Avoid: sixth digit, forked finger, fused finger, missing thumb, shared outline, touching hands, color bleed, mirrored equipment, changed face, changed composition, text or watermark.
~~~

### 04 band 2 cell 4

~~~text
Use case: precise-object-edit
Asset type: isolated mouth repair patch for the facial-rig atlas
Primary request: Repair only the mouth. Replace the malformed mark with a centered, symmetric, compact U-vowel mouth: one small rounded vertical oval directly beneath the nose.
Invariants: preserve the face, centered nose, eyes, eyebrows, hair, ornament, collar, crop, background and natural unblushed skin exactly.
Avoid: tongue, teeth, cat-mouth notch, split lip, skew, double mouth, shifted center, altered nose or altered eyes.
~~~

### 05b row 2 cell 2 — joyful laugh

~~~text
Use case: precise-object-edit
Asset type: corrected single-cell chibi sticker sprite
Input images: Image 1 is the edit target.
Primary request: Edit ONLY the two hands. Replace each malformed starburst hand with a simple, unmistakable normal five-digit open palm. Do not use widely splayed starburst fingers. On BOTH hands, draw FOUR fingers held close together as one orderly fan with FOUR clearly separated fingertips, plus ONE separate thumb on the correct inner side. Total digits per hand = 5, never 6.
Anatomy/orientation: The BARE anatomical RIGHT hand is on the viewer's LEFT. It has exactly four adjoining fingers and one thumb on the palm's viewer-right/inner edge. The BLACK-GLOVED anatomical LEFT hand is on the viewer's RIGHT. It has exactly four adjoining fingers and one thumb on the palm's viewer-left/inner edge. Both palms face forward. Wrists remain attached to their original cuffs.
Hard counting rule: Before finalizing, count each silhouette: 1 thumb + 4 fingers = 5 protruding digits. Delete every sixth protrusion, duplicate thumb, finger stub, or branching contour.
Invariants: Preserve every non-hand element as closely as possible: same Elysia identity, joyful closed eyes and open laugh, pink hairstyle and ornament, torso/outfit, same muted low-saturation chibi line art, same crop/scale/pose, same warm off-white background. Bare hand remains skin-colored with violet cuff. Other hand remains fully black-gloved with white-and-gold cuff.
Constraints: no fused fingers, missing digits, extra digits, color bleed, merged hands, malformed wrists, hand/hair fusion, text, logo, watermark, or added objects. Do not alter the face, hair, clothes, composition, or expression.
Avoid: six fingers, star-shaped hands, oversized hands, shiny AI rendering, saturated pink, character redesign.
~~~

### 05c row 3 cell 2 — celebrate

~~~text
Use case: precise-object-edit
Asset type: corrected single-cell chibi sticker sprite for CELEBRATE
Input images: Image 1 is the exact edit target.
Primary request: Repair ONLY the two raised open hands. Replace each malformed six-digit hand with a simple normal five-digit open palm. The BARE anatomical RIGHT hand is on the viewer's LEFT; it must have exactly ONE thumb on its viewer-right/inner side plus exactly FOUR fingers. The BLACK-GLOVED anatomical LEFT hand is on the viewer's RIGHT; it must have exactly ONE thumb on its viewer-left/inner side plus exactly FOUR fingers.
Hard counting rule: Each hand silhouette must contain exactly five digit protrusions total: 1 thumb + 4 fingers. Count them before finalizing. Remove every sixth protrusion, duplicate thumb, finger stub, or branching contour. Prefer four orderly, clearly separated fingers rather than a starburst shape.
Subject invariants: Preserve Elysia's identity, joyful closed eyes and open-mouth smile, pink hair and ornament, raised-hands celebration pose, torso, clothing, violet cuff on the bare right arm, white-and-gold cuff on the black-gloved left arm, crop, scale, line weight, muted low-saturation chibi palette, and warm off-white background as closely as possible.
Composition/framing: identical portrait crop; both hands remain raised in the same approximate positions with palms facing forward.
Constraints: Change only the hand anatomy necessary to remove the extra digits. No fused digits, missing digits, extra digits, duplicate thumbs, color bleed, merged hands, malformed wrists, or hand-to-hair fusion. Keep the bare hand fully skin-colored and the other hand fully black-gloved. No text, logo, watermark, extra object, or character redesign.
Avoid: six fingers, three- or four-digit hands, star-shaped hands, oversized hands, glossy AI finish, saturated pink, altered face, altered expression, altered outfit.
~~~

### 07 targeted cells

~~~text
Use case: precise-object-edit
Asset type: opaque hand-only repair patch for an existing desktop-pet cell
Primary request: Change only the specified hand. Keep the existing pose, wrist angle, hand size and location. Preserve the exact crop, aspect ratio, Elysia identity, face, hair, costume, line weight, low-saturation palette and warm off-white background.
For 07a r1c2 and r1c3: repair only the anatomical RIGHT bare hand. Complete one continuous dark-brown contour from the thumb through the palm web. Preserve exactly one thumb plus four fingers, the black-and-gold sleeve and violet cuff.
For 07b r2c1 and 07c r3c1: redraw only the open bare anatomical RIGHT hand as exactly one thumb plus four fingers. Delete the sixth digit while retaining the original open-palm gesture. Use clear dark-brown outer contour and finger separation. Keep the hand fully skin-colored and attached to the black-and-gold sleeve with violet cuff.
For 07c r3c4: redraw only the anatomical LEFT black-gloved hand as exactly one thumb plus four fingers. Give every digit an independent readable contour and internal separation line, one continuous near-black exterior outline, and a clean connection to the white-and-gold gauntlet. Separate the glove from adjacent sleeve and costume shapes.
Background: fully opaque warm off-white, matching the target; no transparent patch, gray halo or black box.
Avoid: extra, missing or fused digits; blobbed glove; duplicate thumb; exposed skin through glove; color bleed; changed face or costume; text, logo or watermark.
~~~

## Live2D aligned face master

On 2026-10-01, OpenAI's built-in image-generation tool produced
`live2d-face-master.png`. The first input was the previous transparent
full-body Live2D composite; the second was the project owner's close-up Elysia
face reference (`IMG_2363..JPG`). Only the face from this result is used as an
authoring master. The existing reviewed body, costume, arms, hands, hair, and
accessory source layers remain authoritative.

~~~text
Use case: precise face correction for a layered Live2D authoring master.
Preserve the full-body character's pose, costume, hair silhouette, ornament,
hands, transparent canvas, line treatment, and low-saturation palette. Change
only the face. Use the supplied close-up as facial-proportion authority: a
mature elegant Elysia face, smaller horizontally set blue almond eyes with
more natural spacing, a longer V-shaped jaw, subtle centered nose, and a small
centered neutral mouth. Keep both eyes level and symmetric on one vertical
face axis. Use a calm soft expression with natural unblushed skin. Do not add
red cheeks, a chibi face, oversized eyes, shifted pupils, a crooked nose or
mouth, extra facial marks, text, logo, or background.
~~~

`scripts/build_elysia_live2d_face_layers.py` subsequently extracts and aligns
the active facial layers on a single 2048×2048 coordinate system. The generated
full-body pixels outside that facial authoring region are not copied into the
runtime model.

## Assembly and validation notes

- `05-chibi-stickers.png` was assembled by stacking the five final 05 row strips in order without changing row content.
- `07-desktop-pet-key-poses.png` was assembled by resizing each final row strip to a common width and stacking the four rows in order; the original row strips remain the preferred production masters.
- The former `10-major-surface-key-visuals.png` was removed from v3 because it was an unused concept atlas with no application or packaging reference.
- All RGB/RGBA modes, dimensions and SHA-256 values are recorded in `README.md`.
- The obsolete v1 and v2 review directories were deleted after all retained images and required generation records were consolidated into v3. Unselected candidates and temporary test renders were not kept.
