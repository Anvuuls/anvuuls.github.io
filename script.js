const noBtn = document.getElementById("no");
const yesBigBtn = document.getElementById("yesBig");
const buttons = document.getElementById("buttons");
const message = document.getElementById("message");
const gallery = document.getElementById("gallery");
const photo = document.getElementById("photo");
const music = document.getElementById("music");
music.volume = 0.7;

const photos = [
  "photos/pic1.jpg",
  "photos/pic2.jpg",
  "photos/pic3.jpg",
  "photos/pic4.jpg",
  "photos/pic5.jpg",
  "photos/pic6.jpg",
  "photos/pic7.jpg",
  "photos/pic8.jpg",
  "photos/pic9.jpg",
  "photos/pic10.jpg",
  "photos/pic11.jpg",
  "photos/pic12.jpg",
  "photos/pic13.jpg",
  "photos/pic14.jpg",
  "photos/pic15.jpg",
  "photos/pic16.jpg",
  "photos/pic17.jpg",
  "photos/pic18.jpg",
  "photos/pic19.jpg",
  "photos/pic20.jpg",
  "photos/pic21.jpg"
];

let currentPhoto = 0;

noBtn.onclick = () => {
  buttons.style.display = "none";
  message.textContent = "oh so you dont love me?";

  setTimeout(() => {
    message.textContent = "";
    buttons.style.display = "block";
  }, 6000);  // 6 seconds
};

yesBigBtn.onclick = () => {
  buttons.style.display = "none";
  message.textContent = "you chose correctly, my precious love";

  // Remove the main question
  document.getElementById("question").style.display = "none";

  setTimeout(() => {
    message.textContent = "";
    gallery.classList.remove("hidden");
    photo.src = photos[currentPhoto];

    // Play music after user interaction (mobile-safe)
    music.currentTime = 0;
    music.play().catch(() => {
  console.log("Music blocked until another tap");
  });
  }, 6000);
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
    message.textContent = "I can’t wait for all the years ahead with you. I love you more than I can explain.";
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

