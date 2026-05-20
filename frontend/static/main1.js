// FILE: static/main1.js

document.addEventListener("DOMContentLoaded", () => {
    // STEP 1: Set hero elements visible (no entrance animation)
    const elements = document.querySelectorAll('.main-title span, .subtitle, .buttons');
    elements.forEach(el => {
        if (el) {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        }
    });

    // STEP 2: Spline Robot Viewer Initialization
    const robotViewer = document.querySelector('#robot-background');

    if (robotViewer) {
        console.log('Interactive robot Spline found');

        robotViewer.addEventListener('load', () => {
            console.log('Robot scene loaded - mouse interactions active');
        });

        robotViewer.addEventListener('error', (e) => {
            console.error('Robot loading error:', e);
        });

        setTimeout(() => {
            robotViewer.style.display = 'block';
            robotViewer.style.visibility = 'visible';
            robotViewer.style.opacity = '1';
            console.log('Robot visibility confirmed - cursor interactions enabled');
        }, 1000);
    }

    // STEP 3: Button hover effects
    const loginBtn = document.querySelector('.login-btn');
    const signupBtn = document.querySelector('.signup-btn');

    if (loginBtn) {
        loginBtn.addEventListener('mouseenter', () => {
            loginBtn.style.transform = 'translateY(-3px) scale(1.05)';
            loginBtn.style.boxShadow = '0 8px 30px rgba(0, 0, 0, 0.25)';
        });

        loginBtn.addEventListener('mouseleave', () => {
            loginBtn.style.transform = 'translateY(0) scale(1)';
            loginBtn.style.boxShadow = '0 5px 25px rgba(0, 0, 0, 0.15)';
        });
    }

    if (signupBtn) {
        signupBtn.addEventListener('mouseenter', () => {
            signupBtn.style.transform = 'translateY(-3px) scale(1.05)';
            signupBtn.style.boxShadow = '0 8px 30px rgba(0, 0, 0, 0.25)';
        });

        signupBtn.addEventListener('mouseleave', () => {
            signupBtn.style.transform = 'translateY(0) scale(1)';
            signupBtn.style.boxShadow = '0 5px 25px rgba(0, 0, 0, 0.15)';
        });
    }

    // STEP 4: Prevent scrollbars
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';

    // STEP 5: Signup JS Validation
    const signupForm = document.querySelector('form[action="/signup"]');
    if (signupForm) {
        signupForm.addEventListener('submit', (e) => {
            const emailInput = signupForm.querySelector('input[name="email"]');
            const passwordInput = signupForm.querySelector('input[name="password"]');
            const confirmPasswordInput = signupForm.querySelector('input[name="confirm_password"]');

            const email = emailInput.value.trim();
            const password = passwordInput.value.trim();
            const confirmPassword = confirmPasswordInput.value.trim();

            // Email format check
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (!emailRegex.test(email)) {
                alert('Please enter a valid email address.');
                emailInput.focus();
                e.preventDefault();
                return;
            }

            // Password match check
            if (password !== confirmPassword) {
                alert('Passwords do not match.');
                confirmPasswordInput.focus();
                e.preventDefault();
                return;
            }

            // Password length check (optional)
            if (password.length < 6) {
                alert('Password must be at least 6 characters long.');
                passwordInput.focus();
                e.preventDefault();
                return;
            }
        });
    }
});
