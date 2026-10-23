"""Selectable muscle regions and introductory exercise cues."""
ACE = 'https://www.acefitness.org/resources/everyone/exercise-library/'
NHS = 'https://www.nhs.uk/live-well/exercise/'
# Coordinates describe an illustrative body map, not diagnostic anatomy.
AREAS = [
 ('face','Head & face','Facial and jaw muscles','Jaw relaxation','Let your jaw rest gently, with lips relaxed and teeth apart. Breathe slowly; do not force the jaw.', 'No equipment','https://www.leedsth.nhs.uk/patients/resources/temporomandibular-disorders-tmd/',(150,44,22,27)),
 ('neck','Neck','Neck muscles','Gentle neck rotation','Sit upright. Slowly turn your head within a comfortable range, return to centre, then change sides.','Chair',NHS+'flexibility-exercises/',(150,82,14,12)),
 ('shoulders','Shoulders','Deltoids','Light lateral raise','Stand tall with light weights. Lift your arms out to the sides to a comfortable height, then lower with control.','Light dumbbells',ACE+'body-part/shoulders/',(105,111,24,22)),
 ('chest','Chest','Pectorals','Wall push-up','Place your hands on a wall at chest height. Bend your elbows to bring your chest closer, then gently press away.','Wall',NHS+'strength-exercises/',(150,128,33,25)),
 ('back','Back','Trapezius and latissimus dorsi','Supported dumbbell row','Support one hand on a sturdy bench. Keep your back steady and pull a light weight toward your hip; lower slowly.','Bench and dumbbell',ACE+'body-part/back/',(150,145,35,42)),
 ('core','Abdomen / core','Abdominals and obliques','Bird dog','Start on hands and knees. Reach one arm and the opposite leg without arching your back. Return and switch sides.','Mat','https://www.nasm.org/resource-center/exercise-library/bird-dog',(150,185,28,32)),
 ('arms','Upper arms','Biceps and triceps','Biceps curl','Keep your elbows beside your body. Curl light weights toward your shoulders, then lower slowly.','Light dumbbells',NHS+'strength-exercises/',(91,158,13,29)),
 ('forearms','Forearms','Wrist flexors and extensors','Wrist movement','Rest your forearm on a table. Gently bend the wrist up and down within a comfortable range.','Table','https://www.uhsussex.nhs.uk/resources/range-of-movement-hand-exercises/',(79,216,11,26)),
 ('hands','Hands','Finger and thumb muscles','Open and close hands','Slowly close your fingers into a loose fist, then open and spread them. Keep the movement gentle.','No equipment','https://leedscommunityhealthcare.nhs.uk/our-services-a-z/musculoskeletal-msk/wrist-hand-problems/new-and-undiagnosed-wrist-and-hand-problems/',(72,258,12,17)),
 ('glutes','Hips & glutes','Gluteals and hip muscles','Standing side leg raise','Hold a stable chair. Keep your body upright and lift one leg sideways without leaning, then return and switch sides.','Chair',NHS+'strength-exercises/',(150,239,32,25)),
 ('thighs','Thighs','Quadriceps, hamstrings and adductors','Bodyweight squat','Stand with feet comfortably apart. Move your hips back and bend your knees, keeping them aligned with your feet. Stand up smoothly.','No equipment',ACE+'135/bodyweight-squat/',(129,293,19,42)),
 ('calves','Lower legs','Calves and shin muscles','Calf raise','Hold a stable chair. Rise onto the balls of your feet, then lower your heels slowly.','Chair',NHS+'strength-exercises/',(129,376,14,36)),
 ('feet','Feet','Foot muscles','Towel curl','Sit with a bare foot on a small towel. Gently curl your toes to draw the towel toward you, then release.','Chair and towel','https://www.dbth.nhs.uk/wp-content/uploads/2025/12/Plantar-Fascitis-leaflet.pdf',(126,427,19,12)),
 ('deep','Deep muscles','Diaphragm and spinal stabilisers','Cat?cow','Start on hands and knees. Slowly round your back, then return through neutral into a gentle curve. Avoid forcing the movement.','Mat',ACE+'15/cat-cow/',(150,181,12,21)),
]
GROUPS = [dict(id=a,label=b,muscles=c,exercise=d,steps=e,equipment=f,source=g,shape=h) for a,b,c,d,e,f,g,h in AREAS]


# Hotspots follow the original poster's front/back crops, in source pixels.
HOTSPOTS = {
 'front': [
  ('face','132,30 183,30 199,70 184,122 142,122 126,72'),
  ('neck','145,122 183,122 197,163 128,163'),
  ('shoulders','87,151 125,158 119,204 69,217'),
  ('shoulders','199,156 231,173 253,214 210,208'),
  ('chest','126,164 197,164 210,209 195,247 118,246 111,205'),
  ('core','118,248 196,248 206,326 191,384 130,388 113,324'),
  ('arms','70,218 111,205 106,286 86,357 55,342'),
  ('arms','212,212 253,217 269,302 270,352 235,360 218,292'),
  ('forearms','55,344 86,358 63,422 45,452 21,437'),
  ('forearms','236,362 270,352 275,443 261,463 248,420'),
  ('hands','21,440 45,455 44,496 24,519 0,499 0,463'),
  ('hands','261,465 275,447 275,515 265,522 251,491'),
  ('glutes','131,390 190,387 208,446 168,461 145,461 112,448'),
  ('thighs','109,450 148,465 146,543 133,619 99,621 96,542'),
  ('thighs','171,465 209,449 208,538 194,620 160,620 155,544'),
  ('calves','99,624 131,624 128,679 109,749 83,749 84,686'),
  ('calves','162,624 193,624 191,687 186,751 160,751 150,683'),
  ('feet','83,752 109,752 109,786 137,803 126,818 84,813 71,794'),
  ('feet','161,753 186,753 190,787 204,806 184,818 153,810 150,793'),
 ],
 'back': [
  ('face','113,30 160,30 178,72 162,119 124,124 108,74'),
  ('neck','125,120 163,117 177,162 108,164'),
  ('shoulders','67,151 108,152 100,209 50,210'),
  ('shoulders','180,153 220,172 237,213 188,207'),
  ('back','110,163 175,164 191,222 176,299 164,374 110,374 91,281 99,219'),
  ('arms','49,212 98,211 88,276 73,348 42,343 38,279'),
  ('arms','189,211 236,216 245,289 264,351 231,362 207,288'),
  ('forearms','43,347 72,352 62,415 38,452 13,437 26,393'),
  ('forearms','232,364 264,354 273,422 280,452 255,465 244,421'),
  ('hands','13,440 37,455 43,504 23,523 0,493 0,458'),
  ('hands','255,468 280,454 280,522 264,526 250,499'),
  ('glutes','106,377 165,377 186,410 191,455 161,477 123,477 85,451 89,409'),
  ('thighs','84,455 124,480 120,542 105,620 71,620 71,538'),
  ('thighs','163,480 192,459 195,534 190,621 153,621 145,545'),
  ('calves','72,624 107,624 115,674 103,748 75,748 66,680'),
  ('calves','154,624 188,624 185,691 175,750 149,750 143,680'),
  ('feet','76,752 101,752 104,790 136,808 110,818 70,805'),
  ('feet','149,753 175,753 182,788 214,805 192,818 144,809'),
 ]
}
