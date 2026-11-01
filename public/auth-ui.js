'use strict';
const setup = document.body.dataset.mode === 'setup';
const form = document.getElementById('auth-form');
const errorBox = document.getElementById('auth-error');
const submit = document.getElementById('auth-submit');
if (setup) {
  document.getElementById('auth-title').textContent = 'Configurá tu acceso a Ariel';
  document.getElementById('auth-description').textContent = 'Elegí tu usuario y una contraseña de al menos 12 caracteres. Se guarda una verificación protegida de la contraseña, nunca el texto original.';
  form.elements.password.minLength = 12;
  form.elements.password.autocomplete = 'new-password';
  document.getElementById('auth-confirm-label').hidden = false;
  form.elements.confirmPassword.required = true;
  submit.textContent = 'Crear acceso privado';
}
form.addEventListener('submit', async event => {
  event.preventDefault(); errorBox.textContent = ''; submit.disabled = true;
  try {
    const challengeResponse = await fetch('/api/auth/login-challenge', {cache:'no-store', credentials:'same-origin'});
    if (!challengeResponse.ok) throw new Error('No se pudo preparar el acceso. Volvé a intentar.');
    const challenge = await challengeResponse.json();
    const response = await fetch('/api/auth/' + (setup ? 'setup' : 'login'), {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-CSRF-Token':challenge.csrfToken},
      body:JSON.stringify({username:form.elements.username.value,password:form.elements.password.value,confirmPassword:form.elements.confirmPassword.value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar el acceso');
    form.reset(); location.replace(setup ? '/login' : '/');
  } catch (error) { errorBox.textContent = error.message; }
  finally { submit.disabled = false; }
});
