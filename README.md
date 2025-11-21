Для запуска основного сайта:
python manage.py runserver
Открываем первый раз сайт он начинает загружать всё из Яндекс Диска по API займёт не много времени
Далее для запуска Тг бота с функцией поиска:
python run_bot.py runserver в отдельном терминале желательно


python manage.py update_file_index --workers=32 --batch-size=200 ------ если есть какие то обновление в ЯД



python run.py all              # = run_all.py
python run.py web              # = manage.py runserver
python run.py bot              # = run_bot.py  
python run.py migrate          # = manage.py migrate
python run.py collectstatic    # = manage.py collectstatic
