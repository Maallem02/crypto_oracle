class UserModel {
  final String id;
  final String email;
  final String token;

  const UserModel({
    required this.id,
    required this.email,
    required this.token,
  });

  factory UserModel.fromJson(Map<String, dynamic> json) => UserModel(
        id:    json['id']?.toString() ?? '',
        email: json['email'] ?? '',
        token: json['token'] ?? '',
      );

  Map<String, dynamic> toJson() => {
        'id':    id,
        'email': email,
        'token': token,
      };
}