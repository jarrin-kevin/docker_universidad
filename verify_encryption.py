import gender_guesser.detector as gender

def obtener_sexo(nombre):
    d = gender.Detector()
    sexo = d.get_gender(nombre)
    
    if sexo in ['male', 'mostly_male']:
        return "Masculino"
    elif sexo in ['female', 'mostly_female']:
        return "Femenino"
    elif sexo == "andy":  # Nombre andrógino
        return "Indeterminado"
    else:
        return "Desconocido"

def extraer_nombre(correo):
    # Divide el correo en partes usando el punto como separador y toma la primera parte
    nombre = correo.split('@')[0].split('.')[0]
    return nombre.capitalize()  # Capitaliza la primera letra

# Solicitar correo al usuario
correo = input("Ingrese un correo electrónico: ")
nombre = extraer_nombre(correo)
sexo = obtener_sexo(nombre)

print(f"Nombre detectado: {nombre}")
print(f"Sexo estimado: {sexo}")
