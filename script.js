const yesBtn = document.getElementById("yes");
const yesBigBtn = document.getElementById("yesBig");
const buttons = document.getElementById("buttons");
const message = document.getElementById("message");
const gallery = document.getElementById("gallery");
const photo = document.getElementById("photo");
const music = document.getElementById("music");

const photos = [
  "photos/pic1.jpg",
  "photos/pic2.jpg",
  "photos/pic3.jpg",
  "photos/pic4.jpg"
];

let currentPhoto = 0;

yesBtn.onclick = () => {
  buttons.style.display = "none";
  message.textContent = "oh so you dont love me?";

  setTimeout(() => {
    message.textContent = "";
    buttons.style.display = "block";
  }, 3000);
};

yesBigBtn.onclick = () => {
  buttons.style.display = "none";
  message.textContent = "you chose correctly, my precious love";

  setTimeout(() => {
    message.textContent = "";
    gallery.classList.remove("hidden");
    photo.src = photos[currentPhoto];
    music.play();
  }, 3000);
};

// Swipe logic
let startX = 0;

photo.addEventListener("touchstart", e => {
  startX = e.touches[0].clientX;
});

photo.addEventListener("touchend", e => {
  let endX = e.changedTouches[0].clientX;
  if (startX - endX > 50) {
    nextPhoto();
  }
});

photo.addEventListener("click", nextPhoto);

function nextPhoto() {
  currentPhoto++;
  if (currentPhoto < photos.length) {
    photo.src = photos[currentPhoto];
  } else {
    gallery.innerHTML = "";
    message.textContent = "I cant wait for the many years to come between us, I love you more than I can take.";
  }
}

// Floating hearts with K and G
const heartsContainer = document.getElementById("hearts-container");

setInterval(() => {
  const heart = document.createElement("div");
  heart.className = "heart";
  heart.style.left = Math.random() * 100 + "vw";
  heart.style.animationDuration = (5 + Math.random() * 5) + "s";

  const letter = document.createElement("span");
  letter.textContent = Math.random() > 0.5 ? "K" : "G";

  heart.appendChild(letter);
  heartsContainer.appendChild(heart);

  setTimeout(() => {
    heart.remove();
  }, 10000);
}, 500);

