const navButton = document.querySelector('.nav-toggle');
const nav = document.querySelector('#site-nav');

if (navButton && nav) {
  navButton.addEventListener('click', () => {
    const open = navButton.getAttribute('aria-expanded') === 'true';
    navButton.setAttribute('aria-expanded', String(!open));
    nav.classList.toggle('open', !open);
  });
}

document.querySelectorAll('[data-gallery]').forEach((gallery) => {
  const slides = Array.from(gallery.querySelectorAll('.gallery-slide'));
  const controls = Array.from(gallery.querySelectorAll('.gallery-controls button[data-slide]'));
  const toggle = gallery.querySelector('.gallery-toggle');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let current = 0;
  let timer;
  let paused = reduceMotion;

  const show = (index) => {
    current = (index + slides.length) % slides.length;
    slides.forEach((slide, slideIndex) => {
      const active = slideIndex === current;
      slide.hidden = !active;
      slide.classList.toggle('is-active', active);
    });
    controls.forEach((control, controlIndex) => {
      const active = controlIndex === current;
      control.classList.toggle('is-active', active);
      if (active) control.setAttribute('aria-current', 'true');
      else control.removeAttribute('aria-current');
    });
  };

  const stop = () => window.clearInterval(timer);
  const start = () => {
    stop();
    if (!paused && slides.length > 1) {
      timer = window.setInterval(() => show(current + 1), 3000);
    }
  };

  controls.forEach((control, index) => {
    control.addEventListener('click', () => {
      show(index);
      start();
    });
  });
  if (toggle) {
    toggle.textContent = paused ? 'Play' : 'Pause';
    toggle.addEventListener('click', () => {
      paused = !paused;
      toggle.textContent = paused ? 'Play' : 'Pause';
      if (paused) stop();
      else start();
    });
  }
  gallery.addEventListener('mouseenter', stop);
  gallery.addEventListener('mouseleave', start);
  gallery.addEventListener('focusin', stop);
  gallery.addEventListener('focusout', start);
  show(0);
  start();
});
