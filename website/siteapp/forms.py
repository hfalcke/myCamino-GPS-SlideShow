from django import forms


CONSENT_TEXT = "I have read the privacy notice and agree to the processing of my email for beta access and essential beta communication."


class BetaRegistrationForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.org"}))
    consent = forms.BooleanField(label=CONSENT_TEXT)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="Leave empty")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class ContactForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    subject = forms.CharField(max_length=160)
    message = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 8}))
    consent = forms.BooleanField(label="I agree that my details may be used to answer this message.")
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="Leave empty")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()
