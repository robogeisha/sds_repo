git clone https://github.com/robogeisha/sds_repo.git

cd sds_repo

python3 -m venv rasa-venv
source rasa-venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt


++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

Terminal A
source rasa-venv/bin/activate
rasa run actions --debug


Terminal B
source rasa-venv/bin/activate
rasa train
rasa run --enable-api


Terminal C
source rasa-venv/bin/activate
python3 push_to_talk_voice_bot.py
