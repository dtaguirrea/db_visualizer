from pathlib import Path
from streamlit.testing.v1 import AppTest

from src.dynamic_tables import create_dynamic_table
from src.repository import insert_record

PAGE = str(Path(__file__).resolve().parents[1] / 'pages' / 'analytics.py')


def test_page_empty_and_document_views(database):
    app = AppTest.from_file(PAGE, default_timeout=20)
    app.session_state['logged_in'] = True
    app.run()
    assert not app.exception
    app.radio[0].set_value('Documentos').run()
    assert not app.exception
    app.radio[0].set_value('Relaciones').run()
    assert not app.exception
    assert 'No hay llaves foráneas' in app.info[0].value


def test_page_gender_percentages_and_chart(database):
    with database.connect() as conn:
        create_dynamic_table(conn, 'personas', [
            {'name': 'genero', 'type': 'Opciones', 'options': ['Mujer', 'Hombre']}
        ], '', 'test')
        for value in ['Mujer', 'Hombre', None]:
            insert_record(conn, 'personas', {'genero': value}, 'test')
    app = AppTest.from_file(PAGE, default_timeout=20)
    app.session_state['logged_in'] = True
    app.run()
    assert not app.exception
    assert app.metric[-1].value == '33.33 %'
    app.checkbox[0].uncheck().run()
    assert not app.exception
    assert app.metric[-1].value == '50.00 %'
    app.selectbox(key='analytics_personas_genero_style').set_value('Circular').run()
    assert not app.exception
