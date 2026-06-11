# Publishing FIMsense to GitHub and PyPI

This guide covers everything you need to do manually to publish the package.

---

## Part 1 — GitHub

### Step 1: Install Git (if not already installed)

Download from https://git-scm.com/download/win and run the installer.

### Step 2: Create a GitHub account

Go to https://github.com and sign up (or log in if you already have one).

### Step 3: Create a new repository on GitHub

1. Click the **+** icon → **New repository**
2. Name it `fimsense`
3. Set it to **Public**
4. Do NOT check "Initialize this repository" (you'll push your local files)
5. Click **Create repository**

GitHub will show you a URL like: `https://github.com/YOUR_USERNAME/fimsense.git`

### Step 4: Update the URL in pyproject.toml

Open `pyproject.toml` and replace `tiandan-geo` with your actual GitHub username in:
```
Homepage = "https://github.com/YOUR_USERNAME/fimsense"
Repository = "https://github.com/YOUR_USERNAME/fimsense"
```

### Step 5: Initialize git and push your code

Open a terminal (Anaconda Prompt or PowerShell) and run:

```bash
cd D:\Box\1_PythonCode\RsFimToolSet\fimsense

# Set your identity (one-time setup)
git config --global user.name "Your Name"
git config --global user.email "tiandan.geo@gmail.com"

# Initialize and commit
git init
git add .
git commit -m "Initial release v0.1.0"

# Connect to GitHub and push
git remote add origin https://github.com/YOUR_USERNAME/fimsense.git
git branch -M main
git push -u origin main
```

You will be prompted to authenticate — use your GitHub username and a **Personal Access Token** (not your password). To create one:
1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate a new token with `repo` scope
3. Copy it and use it as your password when git prompts

---

## Part 2 — PyPI

### Step 1: Create a PyPI account

Go to https://pypi.org/account/register/ and register.

You will also need a **TestPyPI** account for testing: https://test.pypi.org/account/register/

### Step 2: Install build tools

```bash
pip install build twine
```

### Step 3: Build the package

From the `fimsense/` directory:

```bash
cd D:\Box\1_PythonCode\RsFimToolSet\fimsense
python -m build
```

This creates a `dist/` folder with two files:
- `fimsense-0.1.0.tar.gz` (source distribution)
- `fimsense-0.1.0-py3-none-any.whl` (wheel)

### Step 4: (Optional) Test on TestPyPI first

```bash
python -m twine upload --repository testpypi dist/*
```

You'll be prompted for your TestPyPI username and password.

To install from TestPyPI to verify it works:
```bash
pip install --index-url https://test.pypi.org/simple/ fimsense
```

### Step 5: Upload to the real PyPI

```bash
python -m twine upload dist/*
```

Enter your PyPI username and password (or use an API token — recommended).

**Using an API token (more secure):**
1. Go to PyPI → Account settings → API tokens → Add API token
2. When twine prompts for username, enter: `__token__`
3. When prompted for password, paste the token (starts with `pypi-`)

### Step 6: Verify the upload

Your package will be at: `https://pypi.org/project/fimsense/`

Users can now install with:
```bash
pip install fimsense
```

---

## Part 3 — Releasing new versions

1. Update the version in `pyproject.toml` (e.g., `version = "0.1.1"`)
2. Commit and tag the release:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.1.1"
   git tag v0.1.1
   git push && git push --tags
   ```
3. Rebuild and upload:
   ```bash
   python -m build
   python -m twine upload dist/*
   ```

---

## Troubleshooting

**"filename already exists"** on PyPI upload → You cannot re-upload the same version. Bump the version number.

**Authentication fails** → Use an API token instead of password (see Step 5 above).

**ImportError: No module named 'osgeo'** → GDAL must be installed via conda, not pip. Add a note to your README pointing users to `conda install -c conda-forge gdal`.

**Git push requires credentials repeatedly** → Run `git config --global credential.helper store` to cache them.
