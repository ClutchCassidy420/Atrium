# Contributing

Thanks for looking. Atrium is a small project, so a few simple rules keep it easy to work on.

- **Try the demo first:** `pip install -r requirements.txt` then `python demo/run_demo.py`.
- **Run the tests:** `pip install pytest` then `pytest` (about a minute). Please add a test with a fix or a feature.
- **Keep the framework generic.** Names of people, companies, hosts and your own systems belong in the instance folder, never in the code.
  Put terms you want checked for your own install in `tests/banned-terms.txt` (it is not committed).
- **One design.** Colors, radius and fonts are CSS variables in `app/static/tokens.css`; pages use those and nothing else.
- **No secrets, no real data** in issues, screenshots or pull requests.
- Open an issue before a big change, so we can agree on the shape of it.
